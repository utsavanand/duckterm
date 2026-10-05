"""Cross-computer messages survive offline computers without granting owner authority."""

import uuid

import pytest

from duckterm.collaboration.store import Store, reference
from duckterm.core.session_api import APIError


class Computer:
    def __init__(self, hub, name, folder):
        self.hub = hub
        self.identity = hub.computer(name)
        self.id = self.identity["computer_id"]
        self.writer = uuid.uuid4().hex
        self.cards = [{"session_key": name, "name": name, "folder": "Project", "state": "busy"}]
        self.ref = reference(self.id, name)
        self.name = name
        hub.bind(self.id, "Project", folder)
        self.sync()

    def sync(self, *operations):
        return self.hub.exchange(
            self.identity["token"],
            {
                **self.identity,
                "writer": self.writer,
                "cards": self.cards,
                "operations": list(operations),
            },
        )

    def ask(self, target, question="Please check commit abc123"):
        return {
            "id": uuid.uuid4().hex,
            "actor": self.name,
            "action": "ask",
            "target": target.ref,
            "question": question,
            "question_id": "fq-" + uuid.uuid4().hex,
        }

    def transition(self, q, action, **fields):
        return {
            "id": uuid.uuid4().hex,
            "actor": self.name,
            "action": action,
            "question_id": q["id"],
            "revision": q["revision"],
            **fields,
        }


@pytest.fixture
def hub(tmp_path):
    store = Store(tmp_path / "coordinator.sqlite")
    store.initialize("dev")
    yield store
    store.close()


def test_two_remotes_exchange_while_mac_offline_then_wake_delivers_once(hub):
    folder = hub.add_folder("Duckterm")
    mac = Computer(hub, "main-local", folder)
    one = Computer(hub, "remote-one", folder)
    two = Computer(hub, "remote-two", folder)
    # No more Mac exchanges until its simulated wake. Remote requests/replies continue.
    ask_remote = one.ask(two)
    ask_mac = one.ask(mac)
    sent = one.sync(ask_remote, ask_mac)
    q = sent["results"][ask_remote["id"]]["value"]
    assert q["delivery"]["outcome"] == "waiting_for_computer"
    received = two.sync()["questions"]
    assert len(received) == 1 and received[0]["id"] == q["id"]
    reply = two.transition(q, "answer", text="Linux checks pass")
    two.sync(reply)
    assert (
        next(x for x in one.sync()["questions"] if x["id"] == q["id"])["answer"]
        == "Linux checks pass"
    )
    # Retry after a lost send response cannot create a second question.
    one.sync(ask_mac)
    wake = mac.sync()
    assert len(wake["questions"]) == 1
    to_mac = wake["questions"][0]
    receipt = mac.transition(to_mac, "receipt")
    mac.sync(receipt)
    mac.sync(receipt)
    assert len(mac.sync()["questions"]) == 1
    pending = next(x for x in one.sync()["questions"] if x["id"] == to_mac["id"])
    assert pending["delivery"]["outcome"] == "delivered"
    read = mac.transition(to_mac, "read")
    mac.sync(read)
    assert mac.sync()["questions"][0]["delivery"]["outcome"] == "read"


def test_durability_idempotency_and_cancel_reply_race(hub, tmp_path):
    folder = hub.add_folder("Duckterm")
    one, two = Computer(hub, "one", folder), Computer(hub, "two", folder)
    op = one.ask(two)
    q = one.sync(op)["results"][op["id"]]["value"]
    reopened = Store(tmp_path / "coordinator.sqlite")
    try:
        one.hub = two.hub = reopened
        assert one.sync(op)["results"][op["id"]]["value"]["id"] == q["id"]
        cancel = one.transition(q, "cancel")
        one.sync(cancel)
        reply = two.transition(q, "answer", text="late answer")
        result = two.sync(reply)["results"][reply["id"]]
        assert result["status"] == 409
        assert one.sync()["questions"][0]["status"] == "cancelled"
        with pytest.raises(APIError, match="different content"):
            one.sync({**op, "question": "changed"})
    finally:
        reopened.close()


def test_names_never_grant_access_and_ungrouping_revokes_before_delivery(hub):
    root = hub.add_folder("Duckterm")
    other = hub.add_folder("Other")
    same_label = hub.add_folder("Duckterm", other)
    one, two = Computer(hub, "one", root), Computer(hub, "two", same_label)
    refused = one.ask(two)
    assert one.sync(refused)["results"][refused["id"]]["status"] == 404
    hub.bind(two.id, "Project", root)
    two.sync()
    op = one.ask(two)
    one.sync(op)
    two.cards[0]["folder"] = ""
    assert two.sync()["questions"] == []
    assert (
        hub.conn.execute(
            "SELECT status FROM questions WHERE id=?", (op["question_id"],)
        ).fetchone()[0]
        == "blocked"
    )
    assert not [x for x in one.sync()["cards"] if x["session_ref"] == two.ref]


def test_second_active_writer_and_foreign_actor_are_refused(hub):
    folder = hub.add_folder("Duckterm")
    one, two = Computer(hub, "one", folder), Computer(hub, "two", folder)
    original = one.writer
    one.writer = uuid.uuid4().hex
    with pytest.raises(APIError, match="Another active service"):
        one.sync()
    one.writer = original
    forged = {**one.ask(two), "actor": two.name}
    assert one.sync(forged)["results"][forged["id"]]["status"] == 404
    hub.revoke(one.id)
    with pytest.raises(APIError, match="revoked"):
        one.sync()


def test_coordinator_move_keeps_folder_identity_and_revokes_pending_access(hub):
    folder = hub.add_folder("Duckterm")
    one, two = Computer(hub, "one", folder), Computer(hub, "two", folder)
    op = one.ask(two)
    one.sync(op)
    hub.place(two.ref, None)
    assert two.sync()["questions"] == []
    assert (
        hub.conn.execute(
            "SELECT status FROM questions WHERE id=?", (op["question_id"],)
        ).fetchone()[0]
        == "blocked"
    )


def test_local_services_durable_offline_send_and_no_cached_access_after_revoke(tmp_path):
    import asyncio

    from duckterm.collaboration.service import Service
    from duckterm.persistence.history import HistoryStore

    hub = Store(tmp_path / "hub.sqlite")
    hub.initialize("coordinator")
    folder = hub.add_folder("Duckterm")
    histories = []
    services = []
    online = [True]

    def create(name):
        history = HistoryStore(tmp_path / name / "history.sqlite")
        histories.append(history)
        for key in [name, name + "-local-peer"]:
            history.record(
                {
                    "_id": key,
                    "_ts": 1,
                    "event_type": "SessionStart",
                    "session_key": key,
                    "test": True,
                }
            )
            history.set_meta(key, name=key, group="Duckterm")
        service = Service(history)
        identity = hub.computer(name)
        hub.bind(identity["computer_id"], "Duckterm", folder)
        service.join(identity, {"ssh_target": "fixture-only"})

        async def exchange(payload):
            if not online[0]:
                raise OSError("fixture disconnect")
            return hub.exchange(identity["token"], payload)

        service.exchange = exchange
        services.append(service)
        return service

    async def run():
        one, two = create("one"), create("two")
        assert await one.sync() and await two.sync() and await one.sync()
        target = two.own_ref("two")
        assert any(
            p["session_ref"] == target
            for p in (await one.discover("one", "self_folder"))["sessions"]
        )
        online[0] = False
        status, queued = await one.ask(
            "one", {"target_session_id": target, "question": "Check this change"}, "offline-send"
        )
        assert status == 202 and queued["transport_status"] == "saved_on_this_computer"
        assert (await one.discover("one", "self_folder"))["cross_computer_status"] == "unavailable"
        assert any(
            p["session_id"] == "one-local-peer"
            for p in (await one.discover("one", "self_folder"))["sessions"]
        )
        online[0] = True
        assert await one.sync()
        incoming = await two.inbox("two")
        assert len(incoming) == 1 and incoming[0]["id"] == queued["id"]
        assert (
            await one.ask(
                "one",
                {"target_session_id": target, "question": "Check this change"},
                "offline-send",
            )
        )[1]["id"] == queued["id"]
        assert len(await two.inbox("two")) == 1
        hub.place(target, None)
        assert await two.inbox("two") == []
        with pytest.raises(APIError, match="no longer shared"):
            await two.get("two", queued["id"])
        for service in services:
            await service.close()

    try:
        asyncio.run(run())
    finally:
        for history in histories:
            history.close()
        hub.close()


def test_pairing_ticket_is_single_identity_retryable_and_expires(hub, monkeypatch):
    import secrets

    from duckterm.collaboration.store import PROTOCOL, now

    folder = hub.add_folder("Project")
    invite = hub.invite("Mac", [{"local_path": "Project", "folder_id": folder}])
    identity = {
        "computer_id": uuid.uuid4().hex,
        "incarnation": uuid.uuid4().hex,
        "token": secrets.token_urlsafe(32),
        "workspace_id": invite["workspace_id"],
        "protocol": PROTOCOL,
    }
    first = hub.claim(invite["ticket"], identity)
    assert hub.claim(invite["ticket"], identity) == first
    with pytest.raises(APIError, match="already used"):
        hub.claim(invite["ticket"], {**identity, "computer_id": uuid.uuid4().hex})
    assert hub.authenticate(identity["token"])["id"] == identity["computer_id"]
    assert (
        hub.conn.execute(
            "SELECT folder FROM bindings WHERE computer=?", (identity["computer_id"],)
        ).fetchone()[0]
        == folder
    )
    expired = hub.invite("Other", [])
    monkeypatch.setattr("duckterm.collaboration.store.now", lambda: now() + 400000)
    with pytest.raises(APIError, match="expired"):
        hub.claim(expired["ticket"], {**identity, "computer_id": uuid.uuid4().hex})


def test_local_move_overrides_old_coordinator_placement_and_narrow_grants(hub):
    root = hub.add_folder("Duckterm")
    child = hub.add_folder("Restricted", root)
    one, two = Computer(hub, "one", root), Computer(hub, "two", root)
    hub.place(two.ref, root)
    two.cards[0]["folder"] = ""
    assert not two.sync()["cards"]
    two.cards[0].update(folder="Project/Restricted", root="Project/Restricted")
    two.sync()
    op = one.ask(two)
    assert one.sync(op)["results"][op["id"]]["status"] == 404
    assert hub.peer(two.ref)["root"] == child
    assert hub.public_card(hub.peer(two.ref))["ancestry"] == [child]


def test_receipts_are_bounded_and_duplicate_question_ids_are_conflicts(hub):
    import json

    folder = hub.add_folder("Project")
    one, two = Computer(hub, "one", folder), Computer(hub, "two", folder)
    op = one.ask(two)
    q = one.sync(op)["results"][op["id"]]["value"]
    duplicate = {**op, "id": uuid.uuid4().hex}
    assert one.sync(duplicate)["results"][duplicate["id"]]["status"] == 409
    two.sync(two.transition(q, "answer", text="x" * 262144))
    response = two.sync(*(two.transition(q, "read") for _ in range(100)))
    assert len(json.dumps(response).encode()) < 400000


def test_http_authentication_and_offline_reply_use_existing_session_routes(tmp_path):
    import asyncio
    import json

    from duckterm.persistence.history import HistoryStore
    from duckterm.server import Server

    async def run():
        servers, listeners = [], []

        async def create(name):
            history = HistoryStore(tmp_path / name / "history.sqlite")
            for key in (name, name + "-local"):
                history.record(
                    {
                        "_id": key,
                        "_ts": 1,
                        "event_type": "SessionStart",
                        "session_key": key,
                        "test": True,
                    }
                )
                history.set_meta(key, name=key, group="Project")
            server = Server(history=history)
            servers.append(server)
            listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
            listeners.append(listener)
            return server, listener.sockets[0].getsockname()[1]

        async def request(port, method, path, body=None, headers=None):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            data = json.dumps(body or {}).encode()
            lines = [
                f"{method} {path} HTTP/1.1",
                f"Host: 127.0.0.1:{port}",
                f"Content-Length: {len(data)}",
                "Connection: close",
            ]
            lines.extend(f"{k}: {v}" for k, v in (headers or {}).items())
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + data)
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            head, content = response.split(b"\r\n\r\n", 1)
            return int(head.split()[1]), json.loads(content)

        try:
            one, one_port = await create("one")
            two, two_port = await create("two")
            one.collaboration.initialize("Coordinator")
            hub = one.collaboration.store
            folder = hub.add_folder("Project")
            identity = hub.setting("identity")
            hub.bind(identity["computer_id"], "Project", folder)
            other = hub.computer("Remote")
            hub.bind(other["computer_id"], "Project", folder)
            two.collaboration.join(other, {"ssh_target": "fixture-only"})
            online = True

            async def exchange(payload):
                if not online:
                    raise OSError("disconnected fixture")
                status, value = await request(
                    one_port,
                    "POST",
                    "/api/v1/collaboration/exchange",
                    payload,
                    {"Authorization": "Computer " + other["token"]},
                )
                if status != 200:
                    raise APIError(status, value["error"])
                return value

            two.collaboration.exchange = exchange
            a = {
                "Authorization": "Bearer "
                + one.history.session_api.enroll("one", {"root": "Project"})["token"]
            }
            b = {
                "Authorization": "Bearer "
                + two.history.session_api.enroll("two", {"root": "Project"})["token"]
            }
            assert await one.collaboration.sync() and await two.collaboration.sync()
            assert (
                await request(one_port, "POST", "/collaboration/initialize", {"name": "forged"}, a)
            )[0] == 403
            assert (await request(one_port, "POST", "/api/v1/collaboration/exchange", {}, a))[
                0
            ] == 401
            assert (await request(one_port, "GET", "/collaboration/status", headers=a))[0] == 403
            peers = (await request(one_port, "GET", "/api/v1/session/peers", headers=a))[1][
                "sessions"
            ]
            target = next(p["session_id"] for p in peers if p["name"] == "two")
            status, sent = await request(
                one_port,
                "POST",
                "/api/v1/session/questions",
                {"target_session_id": target, "question": "Run Linux checks"},
                {**a, "Idempotency-Key": "http-test"},
            )
            assert status == 200
            qid = sent["id"]
            inbox = (await request(two_port, "GET", "/api/v1/session/inbox", headers=b))[1]
            assert [q["id"] for q in inbox["messages"]] == [qid]
            assert two.collaboration.pending_counts()["two"] == 1
            online = False
            status, queued = await request(
                two_port,
                "POST",
                f"/api/v1/session/questions/{qid}/answer",
                {"text": "Linux passed while Mac disconnected"},
                b,
            )
            assert status == 202 and queued["pending_action"] == "answer"
            local = (await request(two_port, "GET", "/api/v1/session/peers", headers=b))[1]
            assert any(p["session_id"] == "two-local" for p in local["sessions"])
            online = True
            assert await two.collaboration.sync()
            result = (
                await request(one_port, "GET", f"/api/v1/session/questions/{qid}", headers=a)
            )[1]
            assert result["status"] == "answered"
            assert result["answer"] == "Linux passed while Mac disconnected"
            hub.change_folder(folder, None, "Renamed project")
            assert not await one.collaboration.sync()
            assert not await two.collaboration.sync()
            assert one.history.session("one")["grp"] == "Renamed project"
            assert two.history.session("two")["grp"] == "Renamed project"
            assert await one.collaboration.sync()
            assert await two.collaboration.sync()
            assert hub.peer(two.collaboration.own_ref("two"))["root"] == folder
            two.history.record(
                {
                    "_id": "private",
                    "_ts": 2,
                    "event_type": "SessionStart",
                    "session_key": "private",
                    "test": True,
                }
            )
            two.history.set_meta("private", name="Never export this private name", group="")
            assert await two.collaboration.sync()
            assert (
                hub.conn.execute(
                    "SELECT 1 FROM peers WHERE ref=?", (two.collaboration.own_ref("private"),)
                ).fetchone()
                is None
            )
            two.history.set_state("two", "stopped")
            assert two.collaboration.pending_counts() == {}

        finally:
            for listener in listeners:
                listener.close()
                await listener.wait_closed()
            for server in servers:
                await server.collaboration.close()
                server.history.close()

    asyncio.run(run())

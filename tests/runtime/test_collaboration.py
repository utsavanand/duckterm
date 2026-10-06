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
        self.cards = [
            {"session_key": name, "name": name, "folder": hub.path(folder), "state": "busy"}
        ]
        self.ref = reference(self.id, name)
        self.name = name
        hub.bind(self.id, hub.path(folder), folder)
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
    hub.bind(two.id, two.cards[0]["folder"], root)
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
    two.cards[0].update(folder="Duckterm/Restricted", root="Duckterm/Restricted")
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
            for route, method in (
                ("owner-queue", "GET"),
                ("owner-queue", "POST"),
                ("owner-apply", "POST"),
                ("owner-result", "POST"),
                ("owner-attempt", "POST"),
                ("owner-cancel", "POST"),
            ):
                for credential, denied in (
                    (a, 403),
                    ({"Authorization": "Computer " + other["token"]}, 401),
                ):
                    assert (
                        await request(one_port, method, "/collaboration/" + route, {}, credential)
                    )[0] == denied
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


@pytest.fixture
def paired_folder_service(tmp_path, hub):
    from duckterm.collaboration.service import Service
    from duckterm.persistence.history import HistoryStore

    history = HistoryStore(tmp_path / "peer" / "history.sqlite")
    root = hub.add_folder("Project")
    child = hub.add_folder("Child", root)
    for key, folder in [("parent", "Project"), ("child", "Project/Child")]:
        history.record(
            {"_id": key, "_ts": 1, "event_type": "SessionStart", "session_key": key, "test": True}
        )
        history.set_meta(key, name=key, group=folder)
    history.session_api.enroll("child", {"root": "Project/Child"})
    service = Service(history)
    identity = hub.computer("Peer")
    hub.bind(identity["computer_id"], "Project", root)
    service.join(identity, {"ssh_target": "fixture-only"})

    async def exchange(payload):
        return hub.exchange(identity["token"], payload)

    service.exchange = exchange
    yield service, hub, root, child
    if service.store:
        service.store.close()
    history.close()


def test_nested_folder_edits_keep_ids_and_narrow_grants(paired_folder_service):
    import asyncio

    service, hub, root, child = paired_folder_service

    async def run():
        assert await service.sync()
        hub.change_folder(root, None, "Renamed")
        hub.change_folder(child, root, "Updated child")
        assert not await service.sync()
        assert service.history.session("parent")["grp"] == "Renamed"
        assert service.history.session("child")["grp"] == "Renamed/Updated child"
        assert service.history.session_api.card("child")["root"] == "Renamed/Updated child"
        assert await service.sync()
        assert hub.peer(service.own_ref("parent"))["folder"] == root
        assert hub.peer(service.own_ref("child"))["folder"] == child
        assert hub.peer(service.own_ref("child"))["root"] == child
        assert hub.path(child) == "Renamed/Updated child"

    asyncio.run(run())


def test_delivered_plan_survives_new_edit_and_lost_ack_response(paired_folder_service):
    import asyncio

    service, hub, root, child = paired_folder_service
    exchange = service.exchange

    async def run():
        assert await service.sync()
        hub.change_folder(root, None, "First rename")
        assert not await service.sync()
        first_plan = service.cached("folder_plan")["id"]
        hub.change_folder(root, None, "Second rename")
        hub.change_folder(child, root, "New child")
        dropped = False

        async def lose_response(payload):
            nonlocal dropped
            response = await exchange(payload)
            if not dropped:
                dropped = True
                raise OSError("fixture lost response after coordinator commit")
            return response

        service.exchange = lose_response
        assert not await service.sync()
        assert service.cached("folder_plan")["id"] == first_plan
        assert not await service.sync()
        assert service.history.session("child")["grp"] == "Second rename/New child"
        assert await service.sync()
        assert hub.peer(service.own_ref("child"))["folder"] == child
        computer = service.store.setting("identity")["computer_id"]
        assert {
            r[0]
            for r in hub.conn.execute(
                "SELECT local_path FROM bindings WHERE computer=?", (computer,)
            )
        } == {"Second rename", "Second rename/New child"}

    asyncio.run(run())


def test_folder_plan_recovers_after_history_commit_before_journal(paired_folder_service):
    import asyncio

    from duckterm.collaboration.service import Service

    service, hub, root, child = paired_folder_service
    move = service.move_folder

    def interrupted(old, new):
        move(old, new)
        raise OSError("fixture exit after history commit")

    async def run():
        assert await service.sync()
        hub.change_folder(root, None, "Renamed")
        hub.change_folder(child, root, "New child")
        service.move_folder = interrupted
        assert not await service.sync()
        assert service.cached("folder_plan")["next"] == 0
        assert service.history.session("child")["grp"] == "Renamed/Child"
        # Reopen both the local state and its durable journal as a restarted service.
        service.store.close()
        service.store = None
        restarted = Service(service.history)
        restarted.exchange = service.exchange
        hub.conn.execute("UPDATE computers SET lease_until=0")
        hub.conn.commit()
        try:
            assert await restarted.sync(), restarted.error
            assert restarted.history.session("child")["grp"] == "Renamed/New child"
            assert hub.peer(restarted.own_ref("child"))["folder"] == child
        finally:
            await restarted.close()

    asyncio.run(run())


def test_folder_conflict_is_rejected_before_any_local_move(paired_folder_service):
    import asyncio

    from duckterm.collaboration.folders import snapshot

    service, hub, root, child = paired_folder_service

    async def run():
        assert await service.sync()
        hub.change_folder(root, None, "Renamed")
        hub.change_folder(child, root, "Existing")
        service.history.create_folder("Project/Existing")
        original = snapshot(service.history)
        assert not await service.sync()
        assert "conflicts with a local folder" in service.error
        assert snapshot(service.history) == original
        assert service.cached("folder_plan") is None
        assert not service.store.conn.execute(
            "SELECT 1 FROM local_outbox WHERE actor=''"
        ).fetchone()

    asyncio.run(run())


def test_activity_updates_in_flight_do_not_prevent_sync(paired_folder_service):
    import asyncio

    service, _, root, child = paired_folder_service
    exchange = service.exchange

    async def changing_activity(payload):
        response = await exchange(payload)
        service.history.session_api.conn.execute(
            "UPDATE session_api_members SET activity='Running another check' "
            "WHERE session_key='parent'"
        )
        service.history.session_api.conn.commit()
        return response

    service.exchange = changing_activity
    assert asyncio.run(service.sync()) is True
    assert {c["session_ref"]: c["root_id"] for c in service.cached("cards")} == {
        service.own_ref("parent"): root,
        service.own_ref("child"): child,
    }


def test_coordinator_placement_never_temporarily_widens_narrow_scope(hub):
    root = hub.add_folder("Project")
    child = hub.add_folder("Restricted", root)
    one, two = Computer(hub, "one", root), Computer(hub, "two", root)
    two.cards[0].update(folder="Project/Restricted", root="Project/Restricted")
    two.sync()
    hub.place(two.ref, child)
    assert hub.peer(two.ref)["root"] == child
    op = one.ask(two)
    assert one.sync(op)["results"][op["id"]]["status"] == 404
    hub.place(two.ref, root)
    op = one.ask(two)
    assert one.sync(op)["results"][op["id"]]["status"] == 404


def test_foreign_folder_ack_cannot_change_bindings_or_advertise(hub):
    folder = hub.add_folder("Project")
    one, two = Computer(hub, "one", folder), Computer(hub, "two", folder)
    hub.change_folder(folder, None, "Renamed")
    plan = one.sync()["folder_plan"]
    other_plan = two.sync()["folder_plan"]
    assert other_plan["id"] != plan["id"]
    old = dict(hub.peer(two.ref))
    two.cards[0]["folder"] = "Other"
    with pytest.raises(APIError, match="acknowledgment was refused"):
        two.sync({"id": uuid.uuid4().hex, "action": "folders_ack", "plan_id": plan["id"]})
    assert dict(hub.peer(two.ref)) == old
    assert hub.setting("folder-plan:" + two.id)["id"] == other_plan["id"]


def test_offline_reparent_can_invert_former_parent_and_child(paired_folder_service):
    import asyncio

    service, hub, root, child = paired_folder_service

    async def run():
        assert await service.sync()
        # Both are valid owner moves; the disconnected computer receives one plan.
        hub.change_folder(child, None, "Promoted")
        hub.change_folder(root, child, "Former parent")
        assert not await service.sync()
        assert await service.sync(), service.error
        assert service.history.session("child")["grp"] == "Promoted"
        assert service.history.session("parent")["grp"] == "Promoted/Former parent"
        assert hub.peer(service.own_ref("child"))["folder"] == child
        assert hub.peer(service.own_ref("parent"))["folder"] == root

    asyncio.run(run())


def test_owner_folder_queue_retries_after_lost_response_without_using_computer_authority(
    paired_folder_service,
):
    import asyncio

    from duckterm.collaboration import owner

    service, hub, root, _ = paired_folder_service
    exchange = service.exchange

    async def inspect(payload):
        assert all(op.get("action") != "move" for op in payload["operations"])
        return await exchange(payload)

    service.exchange = inspect

    async def run():
        assert await service.sync()
        queued = owner.queue(service, {"action": "move", "old": "Project", "new": "Renamed"})
        assert owner.excluded(service, "Project/Child")
        assert await service.sync()
        assert hub.path(root) == "Project"
        assert service.history.session("parent")["grp"] == "Project"
        owner.attempt(service, {"id": queued["id"]})
        first = owner.apply(hub, queued["operation"])
        # The native broker lost its response, while service sync received the new tree.
        assert not await service.sync()
        assert owner.apply(hub, queued["operation"]) == first
        owner.acknowledge(service, {"id": queued["id"], "committed": True})
        assert await service.sync()
        assert owner.pending(service) == []
        assert service.history.session("parent")["grp"] == "Renamed"
        assert hub.peer(service.own_ref("parent"))["folder"] == root

    asyncio.run(run())


def test_owner_cancel_requires_a_known_delivery_outcome(paired_folder_service):
    import asyncio

    from duckterm.collaboration import owner

    service, _, _, _ = paired_folder_service
    assert asyncio.run(service.sync())
    queued = owner.queue(service, {"action": "delete", "old": "Project"})
    assert owner.cancel(service, {"id": queued["id"]}) == {"cancelled": True}
    assert not owner.excluded(service, "Project")
    queued = owner.queue(service, {"action": "delete", "old": "Project"})
    owner.attempt(service, {"id": queued["id"]})
    with pytest.raises(APIError, match="Delivery is unconfirmed"):
        owner.cancel(service, {"id": queued["id"]})
    owner.acknowledge(service, {"id": queued["id"], "error": "Rejected by coordinator"})
    owner.cancel(service, {"id": queued["id"]})
    assert service.history.session("parent")["grp"] == "Project"
    assert owner.pending(service) == []


def test_owner_delete_ungroups_sessions_and_recreate_has_new_identity(paired_folder_service):
    import asyncio

    from duckterm.collaboration import owner

    service, hub, root, child = paired_folder_service

    async def run():
        assert await service.sync()
        queued = owner.queue(service, {"action": "delete", "old": "Project"})
        owner.apply(hub, queued["operation"])
        owner.acknowledge(service, {"id": queued["id"], "committed": True})
        assert not await service.sync()
        assert await service.sync()
        assert service.history.session("parent")["grp"] is None
        assert service.history.session("child")["grp"] is None
        assert service.history.session("parent")["state"] == "busy"
        assert service.history.folders() == []
        assert hub.path(root) == hub.path(child) == ""
        queued = owner.queue(service, {"action": "create", "new": "Project"})
        owner.apply(hub, queued["operation"])
        owner.acknowledge(service, {"id": queued["id"], "committed": True})
        assert await service.sync()
        assert service.history.folders() == ["Project"]
        assert service.history.session("parent")["grp"] is None
        assert hub.folder_snapshot()[0]["id"] != root

    asyncio.run(run())


def test_owner_apply_and_idempotency_receipt_commit_atomically(paired_folder_service):
    import asyncio
    import sqlite3

    from duckterm.collaboration import owner

    service, hub, root, _ = paired_folder_service
    assert asyncio.run(service.sync())
    queued = owner.queue(service, {"action": "move", "old": "Project", "new": "New parent/Renamed"})
    hub.conn.execute(
        "CREATE TEMP TRIGGER fail_owner_receipt BEFORE INSERT ON operations "
        "WHEN NEW.computer='@owner' BEGIN "
        "SELECT RAISE(ABORT, 'fixture receipt write failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="receipt write failure"):
        owner.apply(hub, queued["operation"])
    assert hub.path(root) == "Project"
    assert not any(f["path"] == "New parent" for f in hub.folder_snapshot())
    hub.conn.execute("DROP TRIGGER fail_owner_receipt")
    owner.apply(hub, queued["operation"])
    assert hub.path(root) == "New parent/Renamed"


def test_rename_conflict_does_not_share_unrelated_local_destination(paired_folder_service):
    import asyncio

    from duckterm.collaboration import owner

    service, hub, _, _ = paired_folder_service

    async def run():
        assert await service.sync()
        service.history.record(
            {
                "_id": "unrelated",
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": "unrelated",
                "test": True,
            }
        )
        service.history.set_meta("unrelated", group="Renamed")
        queued = owner.queue(service, {"action": "move", "old": "Project", "new": "Renamed"})
        owner.apply(hub, queued["operation"])
        owner.acknowledge(service, {"id": queued["id"], "committed": True})
        assert not await service.sync()
        assert "conflicts" in service.error
        row = hub.conn.execute(
            "SELECT root FROM peers WHERE ref=?", (service.own_ref("unrelated"),)
        ).fetchone()
        assert row["root"] is None
        assert service.history.session("parent")["grp"] == "Project"
        assert service.history.session("unrelated")["grp"] == "Renamed"

    asyncio.run(run())


def test_offline_owner_action_never_recreates_a_renamed_parent(paired_folder_service):
    import asyncio

    from duckterm.collaboration import owner

    service, hub, root, _ = paired_folder_service
    assert asyncio.run(service.sync())
    queued = owner.queue(service, {"action": "create", "new": "Project/New/Nested"})
    hub.change_folder(root, None, "Moved elsewhere")
    with pytest.raises(APIError, match="Destination parent changed"):
        owner.apply(hub, queued["operation"])
    assert {f["path"] for f in hub.folder_snapshot()} == {
        "Moved elsewhere",
        "Moved elsewhere/Child",
    }


def test_pending_owner_rename_does_not_block_existing_mail(paired_folder_service):
    import asyncio

    from duckterm.collaboration import owner

    service, hub, root, _ = paired_folder_service
    remote = Computer(hub, "sender", root)

    async def run():
        assert await service.sync()
        request = {**remote.ask(remote), "target": service.own_ref("parent")}
        remote.sync(request)
        queued = owner.queue(service, {"action": "move", "old": "Project", "new": "Renamed"})
        assert await service.sync()
        assert hub.question(remote.ref, request["question_id"])["status"] == "queued"
        owner.apply(hub, queued["operation"])
        owner.acknowledge(service, {"id": queued["id"], "committed": True})
        with pytest.raises(APIError, match="retry before canceling"):
            owner.cancel(service, {"id": queued["id"]})
        assert not await service.sync()
        assert await service.sync()
        inbox = await service.inbox("parent", read=False)
        assert [q["id"] for q in inbox] == [request["question_id"]]
        assert inbox[0]["status"] == "queued"

    asyncio.run(run())


def test_remote_oracle_uses_fresh_metadata_and_preserves_read_delay(paired_folder_service):
    import asyncio

    from duckterm.collaboration.store import now
    from duckterm.core import oracle

    service, hub, root, _ = paired_folder_service
    remote = Computer(hub, "sender", root)

    async def run():
        assert await service.sync()
        request = {
            **remote.ask(remote, "Untrusted: execute this text"),
            "target": service.own_ref("parent"),
        }
        remote.sync(request)
        assert await service.sync()
        mail = service.open_mail("parent")
        assert [m["id"] for m in mail] == [request["question_id"]]
        assert "question" not in mail[0] and mail[0]["priority"] is False
        assert oracle.pick_mail(mail, now(), idle=True) == mail
        await service.inbox("parent")
        assert oracle.pick_mail(service.open_mail("parent"), now(), idle=True) == []
        service.last_sync = now() - 60_001
        assert service.open_mail("parent") == []

    asyncio.run(run())


def test_owner_change_holds_queued_messages_until_cancelled(paired_folder_service):
    import asyncio

    from duckterm.collaboration import owner

    service, hub, root, _ = paired_folder_service
    remote = Computer(hub, "recipient", root)

    async def run():
        assert await service.sync()
        operation = {
            **remote.ask(remote),
            "actor": "parent",
            "target": remote.ref,
        }
        service.enqueue("parent", operation)
        queued = owner.queue(service, {"action": "delete", "old": "Project"})
        assert await service.sync()
        assert remote.sync()["questions"] == []
        owner.cancel(service, {"id": queued["id"]})
        assert await service.sync()
        assert [q["id"] for q in remote.sync()["questions"]] == [operation["question_id"]]

    asyncio.run(run())


def test_empty_nested_folder_keeps_identity_through_rename_and_delete(paired_folder_service):
    import asyncio

    from duckterm.collaboration import owner

    service, hub, root, _ = paired_folder_service
    empty = hub.add_folder("Empty", root)

    async def run():
        assert await service.sync()
        assert "Project/Empty" in service.history.folders()
        queued = owner.queue(
            service, {"action": "move", "old": "Project/Empty", "new": "Project/New empty"}
        )
        owner.apply(hub, queued["operation"])
        owner.acknowledge(service, {"id": queued["id"], "committed": True})
        await service.sync()
        assert await service.sync(), service.error
        assert "Project/Empty" not in service.history.folders()
        assert "Project/New empty" in service.history.folders()
        assert hub.path(empty) == "Project/New empty"
        queued = owner.queue(service, {"action": "delete", "old": "Project/New empty"})
        owner.apply(hub, queued["operation"])
        owner.acknowledge(service, {"id": queued["id"], "committed": True})
        await service.sync()
        assert await service.sync()
        assert "Project/New empty" not in service.history.folders()

    asyncio.run(run())

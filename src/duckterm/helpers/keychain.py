"""macOS Security.framework calls; secret values never enter process arguments."""

import ctypes
from ctypes import c_uint32, c_void_p


def _framework() -> ctypes.CDLL:
    api = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    api.SecKeychainFindGenericPassword.argtypes = [
        c_void_p,
        c_uint32,
        ctypes.c_char_p,
        c_uint32,
        ctypes.c_char_p,
        ctypes.POINTER(c_uint32),
        ctypes.POINTER(c_void_p),
        ctypes.POINTER(c_void_p),
    ]
    api.SecKeychainAddGenericPassword.argtypes = [
        c_void_p,
        c_uint32,
        ctypes.c_char_p,
        c_uint32,
        ctypes.c_char_p,
        c_uint32,
        c_void_p,
        ctypes.POINTER(c_void_p),
    ]
    api.SecKeychainItemModifyAttributesAndData.argtypes = [c_void_p, c_void_p, c_uint32, c_void_p]
    api.SecKeychainItemDelete.argtypes = [c_void_p]
    api.SecKeychainItemFreeContent.argtypes = [c_void_p, c_void_p]
    return api


def _release(item: c_void_p) -> None:
    if item.value:
        core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        core.CFRelease.argtypes = [c_void_p]
        core.CFRelease(item)


def access(service: str, operation: str, value: str = "") -> str | None:
    api = _framework()
    encoded = service.encode()
    account = b"duckterm"
    length, data, item = c_uint32(), c_void_p(), c_void_p()
    code = api.SecKeychainFindGenericPassword(
        None,
        len(encoded),
        encoded,
        len(account),
        account,
        ctypes.byref(length),
        ctypes.byref(data),
        ctypes.byref(item),
    )
    if code not in (0, -25300):
        raise RuntimeError(f"Keychain access failed ({code}); unlock your login keychain")
    try:
        if operation == "read":
            return ctypes.string_at(data, length.value).decode() if code == 0 else None
        if operation == "delete":
            result = api.SecKeychainItemDelete(item) if code == 0 else 0
        elif operation == "write":
            secret = value.encode()
            if code == 0:
                result = api.SecKeychainItemModifyAttributesAndData(item, None, len(secret), secret)
            else:
                result = api.SecKeychainAddGenericPassword(
                    None, len(encoded), encoded, len(account), account, len(secret), secret, None
                )
        else:
            raise ValueError("unknown keychain operation")
        if result != 0:
            raise RuntimeError(f"Keychain update failed ({result})")
        return None
    finally:
        if data.value:
            api.SecKeychainItemFreeContent(None, data)
        _release(item)

"""Shared UDS bits: module addresses, opening a client, decoding replies for humans."""

import sys

from udsoncan import Request, configs
from udsoncan.client import Client
from udsoncan.exceptions import NegativeResponseException, TimeoutException

from .bridge import USBISOTPConnection

# (tester -> module, module -> tester)
CLUSTER = (0x714, 0x77E)
MODULES = {
    "engine": ("Engine ECU (01)", 0x7E0, 0x7E8),
    "dsg": ("Transmission (02)", 0x7E1, 0x7E9),
    "cluster": ("Instrument cluster (17)", 0x714, 0x77E),
    "gateway": ("Gateway (19)", 0x710, 0x77A),
}

NRC = {
    0x10: "generalReject", 0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat", 0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError", 0x31: "requestOutOfRange", 0x33: "securityAccessDenied",
    0x35: "invalidKey", 0x36: "exceededNumberOfAttempts", 0x37: "requiredTimeDelayNotExpired",
    0x72: "generalProgrammingFailure", 0x78: "responsePending",
    0x7E: "subFunctionNotSupportedInActiveSession", 0x7F: "serviceNotSupportedInActiveSession",
}
SERVICE = {
    0x10: "DiagnosticSessionControl", 0x11: "ECUReset", 0x14: "ClearDTC", 0x19: "ReadDTC",
    0x22: "ReadDataByIdentifier", 0x23: "ReadMemoryByAddress", 0x27: "SecurityAccess",
    0x2E: "WriteDataByIdentifier", 0x2F: "IOControl", 0x31: "RoutineControl",
    0x3D: "WriteMemoryByAddress", 0x3E: "TesterPresent",
}

UDS_ERRORS = (NegativeResponseException, TimeoutException)


def open_client(port, txid=CLUSTER[0], rxid=CLUSTER[1], timeout=5):
    conn = USBISOTPConnection(port, rxid=rxid, txid=txid)
    return Client(conn, request_timeout=timeout, config=configs.default_client_config)


def request(client, service, data):
    """Returns (response, None) on success or (None, 'name (0xNN)') on a refusal / no answer."""
    try:
        resp = client.send_request(Request(service, data=data))
    except NegativeResponseException as e:
        return None, f"{e.response.code_name} (0x{e.response.code:02X})"
    except TimeoutException:
        return None, "no response"
    if resp is None:
        return None, "no response"
    if not resp.positive:
        return None, f"{resp.code_name} (0x{resp.code:02X})"
    return resp, None


def describe(p):
    """One-line meaning of a raw UDS reply, for the passive listener."""
    if not p:
        return "(empty)"
    sid = p[0]
    if sid == 0x7F and len(p) >= 3:
        return f"REFUSED {SERVICE.get(p[1], f'0x{p[1]:02X}')}: {NRC.get(p[2], '0x%02X' % p[2])}"
    if sid == 0x50:
        return f"session control accepted (session 0x{p[1]:02X})" if len(p) > 1 else "session control accepted"
    if sid == 0x67:
        if len(p) < 2:
            return "SECURITY ACCESS"
        kind = "seed sent" if p[1] % 2 == 1 else "key accepted"
        return f"SECURITY ACCESS ({kind}, level 0x{p[1]:02X})"
    if sid == 0x6E and len(p) >= 3:
        return f"WRITE accepted for data id {p[1]:02X}{p[2]:02X}"
    if sid == 0x62 and len(p) >= 3:
        return f"read of data id {p[1]:02X}{p[2]:02X} ({len(p) - 3} bytes)"
    if sid == 0x7D and len(p) >= 2:
        alen, llen = p[1] & 0x0F, p[1] >> 4
        if len(p) >= 2 + alen + llen:
            addr = int.from_bytes(p[2:2 + alen], "big")
            size = int.from_bytes(p[2 + alen:2 + alen + llen], "big")
            return f"MEMORY WRITE accepted: {size} bytes at 0x{addr:0{alen * 2}X}"
        return "MEMORY WRITE accepted"
    if sid == 0x63:
        return f"memory read ({len(p) - 1} bytes)"
    if sid == 0x7E:
        return "tester present"
    if sid == 0x51:
        return "ECU reset accepted"
    if sid == 0x71:
        return "routine control reply"
    return f"service reply 0x{sid:02X}"


def die(msg):
    print(msg)
    sys.exit(1)

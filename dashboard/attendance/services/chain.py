"""
Blockchain read helper. Safe to import before the node/contract exist:
every function returns an empty/neutral result if the chain is not reachable.

Settings (from dashboard/.env):
  CHAIN_RPC_URL       JSON-RPC address of the node (e.g. a dev tunnel to Ganache/Hardhat)
  CHAIN_TUNNEL_TOKEN  connect token for a private Microsoft dev tunnel (optional)
  CONTRACT_ADDRESS    deployed Attendance contract (optional until deployed)
  CONTRACT_ABI_PATH   ABI json for that contract
"""
import json
import logging
from pathlib import Path

from django.conf import settings

log = logging.getLogger(__name__)


def _web3():
    try:
        from web3 import Web3
    except ImportError:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.CHAIN_TUNNEL_TOKEN:
        headers["X-Tunnel-Authorization"] = f"tunnel {settings.CHAIN_TUNNEL_TOKEN}"
    return Web3(Web3.HTTPProvider(
        settings.CHAIN_RPC_URL,
        request_kwargs={"headers": headers, "timeout": settings.CHAIN_TIMEOUT},
        exception_retry_configuration=None,  # fail fast; the pages poll again anyway
    ))


def node_info():
    """What the node reports, or {"online": False, "error": ...} if it can't be reached."""
    info = {"online": False, "url": settings.CHAIN_RPC_URL, "error": ""}
    w3 = _web3()
    if w3 is None:
        info["error"] = "web3 is not installed (pip install -r requirements.txt)"
        return info
    try:
        info.update(chain_id=w3.eth.chain_id, block_number=w3.eth.block_number, online=True)
    except Exception as e:  # network errors, 401 from a private tunnel, bad URL...
        msg = str(e)
        if "401" in msg:
            msg = "401 Unauthorized: the dev tunnel is private. Make the port public or set CHAIN_TUNNEL_TOKEN."
        info["error"] = msg[:300]
        log.warning("chain node unreachable at %s: %s", settings.CHAIN_RPC_URL, msg)
    return info


def _contract():
    if not settings.CONTRACT_ADDRESS:
        return None
    abi_path = Path(settings.CONTRACT_ABI_PATH)
    if not abi_path.is_file():
        log.warning("CONTRACT_ADDRESS is set but ABI file %s is missing", abi_path)
        return None
    w3 = _web3()
    if w3 is None:
        return None
    try:
        if not w3.is_connected():
            return None
        abi = json.loads(abi_path.read_text(encoding="utf-8"))
        if isinstance(abi, dict):  # Truffle/Hardhat artifact instead of a bare ABI list
            abi = abi["abi"]
        return w3.eth.contract(address=w3.to_checksum_address(settings.CONTRACT_ADDRESS), abi=abi)
    except Exception as e:
        log.warning("could not load contract: %s", e)
        return None


def chain_status():
    """True when the node answers. Used for the online/offline pill."""
    return node_info()["online"]


def contract_ready():
    return _contract() is not None


def read_chain_records():
    """Return attendance records stored on-chain as a list of dicts."""
    c = _contract()
    if c is None:
        return []
    try:
        events = c.events.AttendanceMarked.get_logs(from_block=0)
    except Exception as e:
        log.warning("could not read AttendanceMarked logs: %s", e)
        return []
    return [
        {
            "member_id": e["args"]["memberID"],
            "timestamp": e["args"]["timestamp"],
            "tx_hash": e["transactionHash"].to_0x_hex(),
            "block_number": e["blockNumber"],
        }
        for e in events
    ]

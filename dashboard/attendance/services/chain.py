"""
Blockchain read helper. Safe to import before Ganache/contract exist:
every function returns an empty/neutral result if the chain is not reachable.
Week 5-6: this is where web3.py gets wired to the deployed Attendance contract.
"""
import json
from django.conf import settings


def _contract():
    try:
        from web3 import Web3
    except ImportError:
        return None
    if not settings.CONTRACT_ADDRESS:
        return None
    w3 = Web3(Web3.HTTPProvider(settings.GANACHE_URL))
    if not w3.is_connected():
        return None
    with open(settings.CONTRACT_ABI_PATH) as f:
        abi = json.load(f)
    return w3.eth.contract(address=Web3.to_checksum_address(settings.CONTRACT_ADDRESS), abi=abi)


def chain_status():
    return _contract() is not None


def read_chain_records():
    """Return attendance records stored on-chain as a list of dicts."""
    c = _contract()
    if c is None:
        return []
    # Assumes the contract exposes `records(uint256)` and the array length via events.
    events = c.events.AttendanceMarked.get_logs(from_block=0)
    return [
        {
            "member_id": e["args"]["memberID"],
            "timestamp": e["args"]["timestamp"],
            "tx_hash": e["transactionHash"].hex(),
            "block_number": e["blockNumber"],
        }
        for e in events
    ]

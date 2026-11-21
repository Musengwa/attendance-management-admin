// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

contract Attendance {
    struct AttendanceRecord {
        address recordedBy;
        string memberID;
        uint256 timestamp;
    }

    address public owner;
    AttendanceRecord[] private records;

    event AttendanceMarked(string memberID, uint256 timestamp);

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Only the system can record attendance");
        _;
    }

    function markAttendance(string memory memberID) public onlyOwner {
        records.push(AttendanceRecord(msg.sender, memberID, block.timestamp));
        emit AttendanceMarked(memberID, block.timestamp);
    }

    function getRecordCount() public view returns (uint256) {
        return records.length;
    }

    function getRecord(uint256 index)
        public
        view
        returns (address, string memory, uint256)
    {
        require(index < records.length, "Index out of range");
        AttendanceRecord memory r = records[index];
        return (r.recordedBy, r.memberID, r.timestamp);
    }
}
import pytest

from firmware_list.parse import parse_gbl_filename


def test_parses_zigbee_ncp_filename():
    record = parse_gbl_filename(
        "sonoff_dongle-pmg24_zigbee_ncp_8.2.2.0_460800_sw_flow.gbl"
    )
    assert record == {
        "brand": "sonoff",
        "model": "dongle-pmg24",
        "type": "zigbee_ncp",
        "version": "8.2.2.0",
        "baudRate": 460800,
        "flowControl": "sw_flow",
    }


def test_parses_zigbee_router_with_hw_flow():
    record = parse_gbl_filename(
        "sonoff_dongle-pmg24_zigbee_router_8.2.2.0_115200_hw_flow.gbl"
    )
    assert record["type"] == "zigbee_router"
    assert record["baudRate"] == 115200
    assert record["flowControl"] == "hw_flow"


def test_parses_no_flow_variant():
    record = parse_gbl_filename(
        "sonoff_zbdonglee_zigbee_ncp_8.2.2.0_115200_no_flow.gbl"
    )
    assert record == {
        "brand": "sonoff",
        "model": "zbdonglee",
        "type": "zigbee_ncp",
        "version": "8.2.2.0",
        "baudRate": 115200,
        "flowControl": "no_flow",
    }


def test_version_keeps_openthread_suffix():
    record = parse_gbl_filename(
        "sonoff_dongle-pmg24_openthread_rcp_2.7.2.0_GitHub-fb0446f53"
        "_460800_sw_flow.gbl"
    )
    assert record["type"] == "openthread_rcp"
    assert record["version"] == "2.7.2.0_GitHub-fb0446f53"
    assert record["baudRate"] == 460800


def test_bootloader_has_no_baudrate_or_flow_control():
    record = parse_gbl_filename("nabucasa_skyconnect_bootloader_3.1.2.gbl")
    assert record == {
        "brand": "nabucasa",
        "model": "skyconnect",
        "type": "bootloader",
        "version": "3.1.2",
    }
    assert "baudRate" not in record
    assert "flowControl" not in record


def test_parses_hyphen_joined_stem():
    record = parse_gbl_filename(
        "tubeszb-bm24-zigbee_ncp_8.2.2.0_460800_sw_flow.gbl"
    )
    assert record["brand"] == "tubeszb"
    assert record["model"] == "bm24"
    assert record["type"] == "zigbee_ncp"


def test_rejects_unknown_firmware_type():
    with pytest.raises(ValueError):
        parse_gbl_filename("acme_dongle_zwave_8.2.2.0_460800_sw_flow.gbl")


def test_rejects_non_gbl_file():
    with pytest.raises(ValueError):
        parse_gbl_filename("sonoff_dongle-pmg24_zigbee_ncp_8.2.2.0.txt")

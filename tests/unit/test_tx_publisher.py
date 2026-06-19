"""Smoke test for TxPublisher — verifies it publishes on the expected topic."""

from __future__ import annotations

import struct
import time

import msgpack
import pytest
import zmq


@pytest.fixture()
def pub_sub_pair() -> object:
    addr = "tcp://127.0.0.1:55591"
    from gr_dis.bridge.tx_publisher import TxPublisher

    pub = TxPublisher(addr)
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"tx_audio.")
    sub.setsockopt(zmq.RCVTIMEO, 1000)
    sub.connect(addr)
    time.sleep(0.05)  # slow-joiner
    yield pub, sub
    pub.close()
    sub.close()
    ctx.term()


def test_publish_sends_correct_topic(pub_sub_pair: object) -> None:
    pub, sub = pub_sub_pair
    pcm = struct.pack("<160h", *([100] * 160))
    pub.publish("vhf_ch1", pcm)

    frames = sub.recv_multipart()
    assert frames[0] == b"tx_audio.vhf_ch1"
    hdr = msgpack.unpackb(frames[1], raw=False)
    assert hdr["type"] == "tx_audio"
    assert hdr["n"] == 160
    assert hdr["sr"] == 8000
    assert hdr["fmt"] == "pcm_s16le"
    assert frames[2] == pcm

from pbg_fake_tool import FakeProcess


def test_update():
    p = FakeProcess(config={"k": 2.0})
    assert p.update({"x": 3.0}) == {"y": 6.0}

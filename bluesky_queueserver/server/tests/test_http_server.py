import time as ttime

import pytest

from bluesky_queueserver.manager.tests._common import (  # noqa F401
    re_manager,
    re_manager_pc_copy,
    re_manager_cmd,
    copy_default_profile_collection,
    append_code_to_last_startup_file,
)

from bluesky_queueserver.server.tests.conftest import (  # noqa F401
    SERVER_ADDRESS,
    SERVER_PORT,
    add_plans_to_queue,
    fastapi_server,
    request_to_json,
    wait_for_environment_to_be_created,
    wait_for_queue_execution_to_complete,
    wait_for_manager_state_idle,
)

from bluesky_queueserver.manager.profile_ops import gen_list_of_plans_and_devices


# Plans used in most of the tests: '_plan1' and '_plan2' are quickly executed '_plan3' runs for 5 seconds.
_plan1 = {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"}
_plan2 = {"name": "scan", "args": [["det1", "det2"], "motor", -1, 1, 10], "item_type": "plan"}
_plan3 = {"name": "count", "args": [["det1", "det2"]], "kwargs": {"num": 5, "delay": 1}, "item_type": "plan"}


# fmt: off
@pytest.mark.parametrize("api_call", ["/", "/ping"])
# fmt: on
def test_http_server_ping_handler(re_manager, fastapi_server, api_call):  # noqa F811
    resp = request_to_json("get", api_call)
    assert resp["msg"] == "RE Manager"
    assert resp["manager_state"] == "idle"
    assert resp["items_in_queue"] == 0
    assert resp["running_item_uid"] is None
    assert resp["worker_environment_exists"] is False


def test_http_server_status_handler(re_manager, fastapi_server):  # noqa F811
    resp = request_to_json("get", "/status")
    assert resp["msg"] == "RE Manager"
    assert resp["manager_state"] == "idle"
    assert resp["items_in_queue"] == 0
    assert resp["running_item_uid"] is None
    assert resp["worker_environment_exists"] is False


def test_http_server_queue_get_handler(re_manager, fastapi_server):  # noqa F811
    resp = request_to_json("get", "/queue/get")
    assert resp["items"] == []
    assert resp["running_item"] == {}


def test_http_server_plans_allowed_and_devices(re_manager, fastapi_server):  # noqa F811
    resp1 = request_to_json("get", "/plans/allowed")
    assert isinstance(resp1["plans_allowed"], dict)
    assert len(resp1["plans_allowed"]) > 0
    resp2 = request_to_json("get", "/devices/allowed")
    assert isinstance(resp2["devices_allowed"], dict)
    assert len(resp2["devices_allowed"]) > 0


def test_http_server_queue_item_add_handler_1(re_manager, fastapi_server):  # noqa F811
    resp1 = request_to_json(
        "post",
        "/queue/item/add",
        json={"item": {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"}},
    )
    assert resp1["success"] is True
    assert resp1["qsize"] == 1
    assert resp1["item"]["name"] == "count"
    assert resp1["item"]["args"] == [["det1", "det2"]]
    assert "item_uid" in resp1["item"]

    resp2 = request_to_json("get", "/queue/get")
    assert resp2["items"] != []
    assert len(resp2["items"]) == 1
    assert resp2["items"][0] == resp1["item"]
    assert resp2["running_item"] == {}


# fmt: off
@pytest.mark.parametrize("pos, pos_result, success", [
    (None, 2, True),
    ("back", 2, True),
    ("front", 0, True),
    ("some", None, False),
    (0, 0, True),
    (1, 1, True),
    (2, 2, True),
    (3, 2, True),
    (100, 2, True),
    (-1, 1, True),
    (-2, 0, True),
    (-3, 0, True),
    (-100, 0, True),
])
# fmt: on
def test_http_server_queue_item_add_handler_2(re_manager, fastapi_server, pos, pos_result, success):  # noqa F811

    plan1 = {"name": "count", "args": [["det1"]], "item_type": "plan"}
    plan2 = {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"}

    # Create the queue with 2 entries
    request_to_json("post", "/queue/item/add", json={"item": plan1})
    request_to_json("post", "/queue/item/add", json={"item": plan1})

    # Add another entry at the specified position
    params = {"item": plan2}
    if pos is not None:
        params.update({"pos": pos})
    resp1 = request_to_json("post", "/queue/item/add", json=params)

    assert resp1["success"] is success
    assert resp1["qsize"] == (3 if success else None)
    assert resp1["item"]["name"] == "count"
    assert resp1["item"]["args"] == plan2["args"]
    assert "item_uid" in resp1["item"]

    resp2 = request_to_json("get", "/queue/get")

    assert len(resp2["items"]) == (3 if success else 2)
    assert resp2["running_item"] == {}

    if success:
        assert resp2["items"][pos_result]["args"] == plan2["args"]


def test_http_server_queue_item_add_handler_3(re_manager, fastapi_server):  # noqa F811

    # Unknown plan name
    plan1 = {"item": {"name": "count_test", "args": [["det1", "det2"]], "item_type": "plan"}}
    resp1 = request_to_json("post", "/queue/item/add", json=plan1)
    assert resp1["success"] is False
    assert "Plan 'count_test' is not in the list of allowed plans" in resp1["msg"]

    # Unknown kwarg
    plan2 = {"item": {"name": "count", "args": [["det1", "det2"]], "kwargs": {"abc": 10}, "item_type": "plan"}}
    resp2 = request_to_json("post", "/queue/item/add", json=plan2)
    assert resp2["success"] is False
    assert (
        "Failed to add an item: Plan validation failed: got an unexpected keyword argument 'abc'" in resp2["msg"]
    )

    # Valid plan
    plan3 = {"item": {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"}}
    resp3 = request_to_json("post", "/queue/item/add", json=plan3)
    assert resp3["success"] is True
    assert resp3["qsize"] == 1
    assert resp3["item"]["name"] == "count"
    assert resp3["item"]["args"] == [["det1", "det2"]]
    assert "item_uid" in resp3["item"]

    resp4 = request_to_json("get", "/queue/get")
    assert resp4["items"] != []
    assert len(resp4["items"]) == 1
    assert resp4["items"][0] == resp3["item"]
    assert resp4["running_item"] == {}


def test_http_server_queue_item_add_handler_4(re_manager, fastapi_server):  # noqa: F811
    """
    Add instruction ('queue_stop') to the queue.
    """

    plan1 = {"name": "count", "args": [["det1"]], "item_type": "plan"}
    plan2 = {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"}
    instruction = {"name": "queue_stop", "item_type": "instruction"}

    # Create the queue with 2 entries
    resp1 = request_to_json("post", "/queue/item/add", json={"item": plan1})
    assert resp1["success"] is True, f"resp={resp1}"
    resp2 = request_to_json("post", "/queue/item/add", json={"item": instruction})
    assert resp2["success"] is True, f"resp={resp2}"
    resp3 = request_to_json("post", "/queue/item/add", json={"item": plan2})
    assert resp3["success"] is True, f"resp={resp3}"

    resp4 = request_to_json("get", "/queue/get")
    assert len(resp4["items"]) == 3
    assert resp4["items"][0]["item_type"] == "plan"
    assert resp4["items"][1]["item_type"] == "instruction"
    assert resp4["items"][2]["item_type"] == "plan"


def test_http_server_queue_item_add_handler_5_fail(re_manager, fastapi_server):  # noqa F811
    """
    Failing case: call without sending a plan.
    """
    resp1 = request_to_json("post", "/queue/item/add", json={})
    assert resp1["success"] is False
    assert resp1["qsize"] is None
    assert resp1["item"] is None
    assert "Incorrect request format: request contains no item info" in resp1["msg"]


# fmt: on
@pytest.mark.parametrize("replace", [None, False, True])
# fmt: off
def test_http_server_queue_item_update_1(re_manager, fastapi_server, replace):  # noqa F811
    """
    Basic test for `/queue/item/update` API.
    """
    resp1 = request_to_json("post", "/queue/item/add", json={"item": _plan1})
    assert resp1["success"] is True, f"resp={resp1}"
    assert resp1["qsize"] == 1
    assert resp1["item"]["name"] == _plan1["name"]
    assert resp1["item"]["args"] == _plan1["args"]
    assert "item_uid" in resp1["item"]

    plan = resp1["item"]
    uid = plan["item_uid"]

    plan_changed = plan.copy()
    plan_new_args = [["det1"]]
    plan_changed["args"] = plan_new_args

    params = {"item": plan_changed}
    if replace is not None:
        params["replace"] = replace

    resp2 = request_to_json("post", "/queue/item/update", json=params)
    assert resp2["success"] is True
    assert resp2["qsize"] == 1
    assert resp2["item"]["name"] == _plan1["name"]
    assert resp2["item"]["args"] == plan_new_args
    assert "item_uid" in resp2["item"]
    if replace:
        assert resp2["item"]["item_uid"] != uid
    else:
        assert resp2["item"]["item_uid"] == uid

    resp3 = request_to_json("get", "/queue/get")
    assert resp3["items"] != []
    assert len(resp3["items"]) == 1
    assert resp3["items"][0] == resp2["item"]
    assert resp3["running_item"] == {}


# fmt: on
@pytest.mark.parametrize("replace", [None, False, True])
# fmt: off
def test_http_server_queue_item_update_2_fail(re_manager, fastapi_server, replace):  # noqa F811
    """
    Failing cases for `queue_item_update`: submitted item UID does not match any UID in the queue.
    """
    resp1 = request_to_json("post", "/queue/item/add", json={"item": _plan1})
    assert resp1["success"] is True
    assert resp1["qsize"] == 1
    assert resp1["item"]["name"] == _plan1["name"]
    assert resp1["item"]["args"] == _plan1["args"]
    assert "item_uid" in resp1["item"]

    plan = resp1["item"]

    plan_changed = plan.copy()
    plan_changed["args"] = [["det1"]]
    plan_changed["item_uid"] = "incorrect_uid"

    params = {"item": plan_changed}
    if replace is not None:
        params["replace"] = replace

    resp2 = request_to_json("post", "/queue/item/update", json=params)
    assert resp2["success"] is False
    assert resp2["msg"] == "Failed to add an item: Failed to replace item: " \
                           "Item with UID 'incorrect_uid' is not in the queue"

    resp3 = request_to_json("get", "/queue/get")
    assert resp3["items"] != []
    assert len(resp3["items"]) == 1
    assert resp3["items"][0] == plan
    assert resp3["running_item"] == {}


def test_http_server_queue_item_get_remove_handler_1(re_manager, fastapi_server):  # noqa F811

    add_plans_to_queue()

    resp1 = request_to_json("get", "/queue/get")
    assert resp1["items"] != []
    assert len(resp1["items"]) == 3
    assert resp1["running_item"] == {}

    resp2 = request_to_json("post", "/queue/item/get", json={})
    assert resp2["success"] is True
    assert resp2["item"]["name"] == "count"
    assert resp2["item"]["args"] == [["det1", "det2"]]
    assert "item_uid" in resp2["item"]

    resp3 = request_to_json("post", "/queue/item/remove", json={})
    assert resp3["success"] is True
    assert resp3["qsize"] == 2
    assert resp3["item"]["name"] == "count"
    assert resp3["item"]["args"] == [["det1", "det2"]]
    assert "item_uid" in resp3["item"]


# fmt: off
@pytest.mark.parametrize("pos, pos_result, success", [
    (None, 2, True),
    ("back", 2, True),
    ("front", 0, True),
    ("some", None, False),
    (0, 0, True),
    (1, 1, True),
    (2, 2, True),
    (3, None, False),
    (100, None, False),
    (-1, 2, True),
    (-2, 1, True),
    (-3, 0, True),
    (-4, 0, False),
    (-100, 0, False),
])
# fmt: on
def test_http_server_queue_item_get_remove_handler_2(
    re_manager, fastapi_server, pos, pos_result, success  # noqa F811
):
    plans = [
        {"name": "count", "args": [["det1"]], "item_type": "plan"},
        {"name": "count", "args": [["det2"]], "item_type": "plan"},
        {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"},
    ]
    for plan in plans:
        request_to_json("post", "/queue/item/add", json={"item": plan})

    # Remove entry at the specified position
    params = {} if pos is None else {"pos": pos}

    # Testing '/queue/item/get'
    resp1 = request_to_json("post", "/queue/item/get", json=params)
    assert resp1["success"] is success
    if success:
        assert resp1["item"]["args"] == plans[pos_result]["args"]
        assert "item_uid" in resp1["item"]
        assert resp1["msg"] == ""
    else:
        assert resp1["item"] == {}
        assert "Failed to get an item" in resp1["msg"]

    # Testing '/queue/item/remove'
    resp2 = request_to_json("post", "/queue/item/remove", json=params)
    assert resp2["success"] is success
    assert resp2["qsize"] == (2 if success else None)
    if success:
        assert resp2["item"]["args"] == plans[pos_result]["args"]
        assert "item_uid" in resp2["item"]
        assert resp2["msg"] == ""
    else:
        assert resp2["item"] == {}
        assert "Failed to remove an item" in resp2["msg"]

    resp3 = request_to_json("get", "/queue/get")
    assert len(resp3["items"]) == (2 if success else 3)
    assert resp3["running_item"] == {}


def test_http_server_queue_item_get_remove_handler_3(re_manager, fastapi_server):  # noqa F811
    """
    Get and remove elements using plan UID. Successful and failing cases.
    Note: the test is derived from ZMQ API test ``test_zmq_api_queue_item_get_remove_3()``
    """
    request_to_json("post", "/queue/item/add", json={"item": _plan3})
    request_to_json("post", "/queue/item/add", json={"item": _plan2})
    request_to_json("post", "/queue/item/add", json={"item": _plan1})

    resp1 = request_to_json("get", "/queue/get")
    plans_in_queue = resp1["items"]
    assert len(plans_in_queue) == 3

    # Get and then remove plan 2 from the queue
    uid = plans_in_queue[1]["item_uid"]
    resp2a = request_to_json("post", "/queue/item/get", json={"uid": uid})
    assert resp2a["item"]["item_uid"] == plans_in_queue[1]["item_uid"]
    assert resp2a["item"]["name"] == plans_in_queue[1]["name"]
    assert resp2a["item"]["args"] == plans_in_queue[1]["args"]
    resp2b = request_to_json("post", "/queue/item/remove", json={"uid": uid})
    assert resp2b["item"]["item_uid"] == plans_in_queue[1]["item_uid"]
    assert resp2b["item"]["name"] == plans_in_queue[1]["name"]
    assert resp2b["item"]["args"] == plans_in_queue[1]["args"]

    # Start the first plan (this removes it from the queue)
    #   Also the rest of the operations will be performed on a running queue.
    resp3 = request_to_json("post", "/environment/open")
    assert resp3["success"] is True
    assert wait_for_environment_to_be_created(10)

    resp4 = request_to_json("post", "/queue/start")
    assert resp4["success"] is True

    ttime.sleep(1)
    uid = plans_in_queue[0]["item_uid"]
    resp5a = request_to_json("post", "/queue/item/get", json={"uid": uid})
    assert resp5a["success"] is False
    assert "is currently running" in resp5a["msg"]
    resp5b = request_to_json("post", "/queue/item/remove", json={"uid": uid})
    assert resp5b["success"] is False
    assert "Can not remove an item which is currently running" in resp5b["msg"]

    uid = "nonexistent"
    resp6a = request_to_json("post", "/queue/item/get", json={"uid": uid})
    assert resp6a["success"] is False
    assert "not in the queue" in resp6a["msg"]
    resp6b = request_to_json("post", "/queue/item/remove", json={"uid": uid})
    assert resp6b["success"] is False
    assert "not in the queue" in resp6b["msg"]

    # Remove the last entry
    uid = plans_in_queue[2]["item_uid"]
    resp7a = request_to_json("post", "/queue/item/get", json={"uid": uid})
    assert resp7a["success"] is True
    resp7b = request_to_json("post", "/queue/item/remove", json={"uid": uid})
    assert resp7b["success"] is True

    ttime.sleep(10)  # TODO: wait for the queue processing to be completed

    state = request_to_json("get", "/status")
    assert state["items_in_queue"] == 0
    assert state["items_in_history"] == 1


def test_http_server_queue_item_get_remove_handler_4_failing(re_manager, fastapi_server):  # noqa F811
    """
    Failing cases that are not tested in other places.
    Note: derived from ``test_zmq_api_queue_item_get_remove_4_failing()``
    """
    # Ambiguous parameters
    resp1 = request_to_json("post", "/queue/item/get", json={"pos": 5, "uid": "some_uid", "item_uid": "plan"})
    assert resp1["success"] is False
    assert "Ambiguous parameters" in resp1["msg"]


# fmt: off
@pytest.mark.parametrize("params, src, order, success, msg", [
    ({"pos": 1, "pos_dest": 1}, 1, [0, 1, 2], True, ""),
    ({"pos": 1, "pos_dest": 0}, 1, [1, 0, 2], True, ""),
    ({"pos": 1, "pos_dest": 2}, 1, [0, 2, 1], True, ""),
    ({"pos": "front", "pos_dest": "back"}, 0, [1, 2, 0], True, ""),
    ({"pos": "back", "pos_dest": "front"}, 2, [2, 0, 1], True, ""),
    ({"uid": 1, "pos_dest": 0}, 1, [1, 0, 2], True, ""),
    ({"uid": 1, "pos_dest": 2}, 1, [0, 2, 1], True, ""),
    ({"uid": 1, "pos_dest": "front"}, 1, [1, 0, 2], True, ""),
    ({"uid": 1, "pos_dest": "back"}, 1, [0, 2, 1], True, ""),
    ({"uid": 0, "before_uid": 0}, 0, [0, 1, 2], True, ""),
    ({"uid": 0, "before_uid": 2}, 0, [1, 0, 2], True, ""),
    ({"uid": 0, "after_uid": 2}, 0, [1, 2, 0], True, ""),
    ({"uid": 2, "before_uid": 0}, 2, [2, 0, 1], True, ""),
    ({"uid": 2, "after_uid": 0}, 2, [0, 2, 1], True, ""),
    ({"pos": 50, "after_uid": 0}, 2, [], False, "Source plan (position 50) was not found"),
    ({"uid": 3, "after_uid": 0}, 2, [], False, "Source plan (UID 'nonexistent') was not found"),
    ({"pos": 1, "pos_dest": 50}, 2, [], False, "Destination plan (position 50) was not found"),
    ({"uid": 1, "after_uid": 3}, 2, [], False, "Destination plan (UID 'nonexistent') was not found"),
    ({"uid": 1, "before_uid": 3}, 2, [], False, "Destination plan (UID 'nonexistent') was not found"),
    ({"after_uid": 0}, 2, [], False, "Source position or UID is not specified"),
    ({"pos": 1}, 2, [], False, "Destination position or UID is not specified"),
    ({"pos": 1, "uid": 1, "after_uid": 0}, 2, [], False, "Ambiguous parameters"),
    ({"pos": 1, "pos_dest": 1, "after_uid": 0}, 2, [], False, "Ambiguous parameters"),
    ({"pos": 1, "before_uid": 0, "after_uid": 0}, 2, [], False, "Ambiguous parameters"),
])
# fmt: on
def test_http_server_move_plan_1(re_manager, fastapi_server, params, src, order, success, msg):  # noqa F811
    """
    The tests are derived from the ZMQ API tests. The number of tests are reduced to save time.
    """
    plans = [
        {"name": "count", "args": [["det1"]], "item_type": "plan"},
        {"name": "count", "args": [["det2"]], "item_type": "plan"},
        {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"},
    ]
    for plan in plans:
        request_to_json("post", "/queue/item/add", json={"item": plan})

    resp1 = request_to_json("get", "/queue/get")
    queue = resp1["items"]
    assert len(queue) == 3

    item_uids = [_["item_uid"] for _ in queue]
    # Add one more 'nonexistent' uid (that is not in the queue)
    item_uids.append("nonexistent")

    # Replace indices with actual UIDs that will be sent to the function
    if "uid" in params:
        params["uid"] = item_uids[params["uid"]]
    if "before_uid" in params:
        params["before_uid"] = item_uids[params["before_uid"]]
    if "after_uid" in params:
        params["after_uid"] = item_uids[params["after_uid"]]

    resp2 = request_to_json("post", "/queue/item/move", json=params)
    if success:
        assert resp2["success"] is True
        assert resp2["item"] == queue[src]
        assert resp2["qsize"] == len(plans)
        assert resp2["msg"] == ""

        # Compare the order of UIDs in the queue with the expected order
        item_uids_reordered = [item_uids[_] for _ in order]
        resp3 = request_to_json("get", "/queue/get")
        item_uids_from_queue = [_["item_uid"] for _ in resp3["items"]]

        assert item_uids_from_queue == item_uids_reordered

    else:
        assert resp2["success"] is False
        assert msg in resp2["msg"]


def test_http_server_open_environment_handler(re_manager, fastapi_server):  # noqa F811
    resp1 = request_to_json("post", "/environment/open")
    assert resp1 == {"success": True, "msg": ""}

    assert wait_for_environment_to_be_created(10), "Timeout"

    resp2 = request_to_json("post", "/environment/open")
    assert resp2 == {"success": False, "msg": "RE Worker environment already exists."}


def test_http_server_close_environment_handler(re_manager, fastapi_server):  # noqa F811
    resp1 = request_to_json("post", "/environment/open")
    assert resp1 == {"success": True, "msg": ""}

    assert wait_for_environment_to_be_created(10), "Timeout"

    resp2 = request_to_json("post", "/environment/close")
    assert resp2 == {"success": True, "msg": ""}

    ttime.sleep(3)  # TODO: API needed to test if environment is closed. Use delay for now.

    resp3 = request_to_json("post", "/environment/close")
    assert resp3 == {"success": False, "msg": "RE Worker environment does not exist."}


def test_http_server_queue_start_handler(re_manager, fastapi_server):  # noqa F811

    add_plans_to_queue()

    resp1 = request_to_json("post", "/queue/start")
    assert resp1 == {"success": False, "msg": "RE Worker environment does not exist."}

    resp2 = request_to_json("post", "/environment/open")
    assert resp2 == {"success": True, "msg": ""}
    resp2a = request_to_json("get", "/queue/get")
    assert len(resp2a["items"]) == 3
    assert resp2a["running_item"] == {}

    assert wait_for_environment_to_be_created(10), "Timeout"

    resp3 = request_to_json("post", "/queue/start")
    assert resp3 == {"success": True, "msg": ""}

    ttime.sleep(1)
    # The plan is currently being executed. 'get_queue' is expected to return currently executed plan.
    resp4 = request_to_json("get", "/queue/get")
    assert len(resp4["items"]) == 2
    assert resp4["running_item"]["name"] == "count"  # Check name of the running plan

    ttime.sleep(25)  # Wait until all plans are executed

    resp4 = request_to_json("get", "/queue/get")
    assert len(resp4["items"]) == 0
    assert resp2a["running_item"] == {}


# fmt: off
@pytest.mark.parametrize("option_pause, option_continue", [
    ("deferred", "resume"),
    ("immediate", "resume"),
    ("deferred", "stop"),
    ("deferred", "abort"),
    ("deferred", "halt")
])
# fmt: on
def test_http_server_re_pause_continue_handlers(
    re_manager, fastapi_server, option_pause, option_continue  # noqa F811
):
    resp1 = request_to_json("post", "/environment/open")
    assert resp1 == {"success": True, "msg": ""}

    assert wait_for_environment_to_be_created(10), "Timeout"

    resp2 = request_to_json(
        "post",
        "/queue/item/add",
        json={
            "item": {
                "name": "count",
                "args": [["det1", "det2"]],
                "kwargs": {"num": 10, "delay": 1},
                "item_type": "plan",
            }
        },
    )
    assert resp2["success"] is True
    assert resp2["qsize"] == 1
    assert resp2["item"]["name"] == "count"
    assert resp2["item"]["args"] == [["det1", "det2"]]
    assert "item_uid" in resp2["item"]

    resp3 = request_to_json("post", "/queue/start")
    assert resp3 == {"success": True, "msg": ""}
    ttime.sleep(3.5)  # Let some time pass before pausing the plan (fractional number of seconds)
    resp3a = request_to_json("post", "/re/pause", json={"option": option_pause})
    assert resp3a == {"msg": "", "success": True}
    ttime.sleep(2)  # TODO: API is needed
    resp3b = request_to_json("get", "/queue/get")
    assert len(resp3b["items"]) == 0  # The plan is paused, but it is not in the queue
    assert resp3b["running_item"] != {}  # Running plan is set

    resp4 = request_to_json("post", f"/re/{option_continue}")
    assert resp4 == {"msg": "", "success": True}

    ttime.sleep(15)  # TODO: we need to wait for plan completion

    resp4a = request_to_json("get", "/queue/get")
    # The plan returns to the queue if it is stopped
    assert len(resp4a["items"]) == 0 if option_continue == "resume" else 1
    assert resp4a["running_item"] == {}


def test_http_server_close_print_db_uids_handler(re_manager, fastapi_server):  # noqa F811

    add_plans_to_queue()

    resp1 = request_to_json("post", "/environment/open")
    assert resp1 == {"success": True, "msg": ""}

    assert wait_for_environment_to_be_created(10), "Timeout"

    resp2 = request_to_json("post", "/queue/start")
    assert resp2 == {"success": True, "msg": ""}

    ttime.sleep(15)

    resp2a = request_to_json("get", "/queue/get")
    assert len(resp2a["items"]) == 0
    assert resp2a["running_item"] == {}


def test_http_server_clear_queue_handler_1(re_manager, fastapi_server):  # noqa F811

    add_plans_to_queue()

    resp1 = request_to_json("get", "/queue/get")
    assert len(resp1["items"]) == 3

    resp2 = request_to_json("post", "/queue/clear")
    assert resp2["success"] is True
    assert resp2["msg"] == ""

    resp3 = request_to_json("get", "/queue/get")
    assert len(resp3["items"]) == 0


def test_http_server_plan_history(re_manager, fastapi_server):  # noqa F811
    # Select very short plan
    plan = {"item": {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"}}
    request_to_json("post", "/queue/item/add", json=plan)
    request_to_json("post", "/queue/item/add", json=plan)
    request_to_json("post", "/queue/item/add", json=plan)

    request_to_json("post", "/environment/open")
    assert wait_for_environment_to_be_created(10), "Timeout"

    request_to_json("post", "/queue/start")
    ttime.sleep(5)

    resp1 = request_to_json("get", "/history/get")
    assert len(resp1["items"]) == 3
    assert resp1["items"][0]["name"] == "count"

    resp2 = request_to_json("post", "/history/clear")
    assert resp2["success"] is True

    resp3 = request_to_json("get", "/history/get")
    assert resp3["items"] == []


def test_http_server_manager_kill(re_manager, fastapi_server):  # noqa F811

    request_to_json("post", "/environment/open")
    assert wait_for_environment_to_be_created(10), "Timeout"

    resp = request_to_json("post", "/test/manager/kill")
    assert resp["success"] is False
    assert "ZMQ communication error:" in resp["msg"]

    ttime.sleep(10)

    resp = request_to_json("get", "/status")
    assert resp["msg"] == "RE Manager"
    assert resp["manager_state"] == "idle"
    assert resp["items_in_queue"] == 0
    assert resp["running_item_uid"] is None
    assert resp["worker_environment_exists"] is True


# fmt: off
@pytest.mark.parametrize("option", [None, "safe_on", "safe_off"])
# fmt: on
def test_http_server_manager_stop_handler_1(re_manager, fastapi_server, option):  # noqa F811

    request_to_json("post", "/environment/open")
    assert wait_for_environment_to_be_created(10), "Timeout"

    kwargs = {"json": {"option": option} if option else {}}
    resp1 = request_to_json("post", "/manager/stop", **kwargs)
    assert resp1["success"] is True

    assert re_manager.check_if_stopped() is True


# fmt: off
@pytest.mark.parametrize("option", [None, "safe_on", "safe_off"])
# fmt: on
def test_http_server_manager_stop_handler_2(re_manager, fastapi_server, option):  # noqa F811

    add_plans_to_queue()

    request_to_json("post", "/environment/open")
    assert wait_for_environment_to_be_created(10), "Timeout"

    request_to_json("post", "/queue/start")

    ttime.sleep(2)
    resp = request_to_json("get", "/status")
    assert resp["msg"] == "RE Manager"
    assert resp["manager_state"] == "executing_queue"
    assert resp["items_in_queue"] == 2
    assert resp["running_item_uid"] is not None
    assert resp["items_in_history"] == 0
    assert resp["worker_environment_exists"] is True

    # Attempt to stop
    kwargs = {"json": {"option": option} if option else {}}
    resp1 = request_to_json("post", "/manager/stop", **kwargs)
    assert resp1["success"] == (option == "safe_off")

    if option == "safe_off":
        assert re_manager.check_if_stopped() is True

    else:
        # The queue is expected to be running
        ttime.sleep(15)
        resp = request_to_json("get", "/status")
        assert resp["msg"] == "RE Manager"
        assert resp["manager_state"] == "idle"
        assert resp["items_in_queue"] == 0
        assert resp["items_in_history"] == 3
        assert resp["running_item_uid"] is None
        assert resp["worker_environment_exists"] is True


# fmt: off
@pytest.mark.parametrize("deactivate", [False, True])
# fmt: on
def test_http_server_queue_stop(re_manager, fastapi_server, deactivate):  # noqa F811
    """
    Methods ``queue_stop_activate`` and ``queue_stop_deactivate``.
    """
    add_plans_to_queue()

    request_to_json("post", "/environment/open")
    assert wait_for_environment_to_be_created(10), "Timeout"

    # Queue is not running, so the request is expected to fail
    resp1 = request_to_json("post", "/queue/stop")
    assert resp1["success"] is False
    status = request_to_json("get", "/status")
    assert status["queue_stop_pending"] is False

    request_to_json("post", "/queue/start")
    ttime.sleep(2)
    status = request_to_json("get", "/status")
    assert status["manager_state"] == "executing_queue"

    resp2 = request_to_json("post", "/queue/stop")
    assert resp2["success"] is True
    status = request_to_json("get", "/status")
    assert status["queue_stop_pending"] is True

    if deactivate:
        ttime.sleep(1)

        resp3 = request_to_json("post", "/queue/stop/cancel")
        assert resp3["success"] is True
        status = request_to_json("get", "/status")
        assert status["queue_stop_pending"] is False

    ttime.sleep(15)

    status = request_to_json("get", "/status")
    assert status["manager_state"] == "idle"
    assert status["items_in_queue"] == (0 if deactivate else 2)
    assert status["items_in_history"] == (3 if deactivate else 1)
    assert status["running_item_uid"] is None
    assert status["worker_environment_exists"] is True
    assert status["queue_stop_pending"] is False


# fmt: off
@pytest.mark.parametrize("suffix, expected_n_items", [
    ("/active", 1),
    ("/open", 1),
    ("/closed", 0),
])
# fmt: on
def test_http_server_re_runs(re_manager, fastapi_server, suffix, expected_n_items):  # noqa F811
    """
    Basic test for ``/re/run/...`` API. The API is tested on a single run plan.
    """
    resp1 = request_to_json("post", "/queue/item/add", json={"item": _plan3})
    assert resp1["success"] is True
    assert resp1["qsize"] == 1

    request_to_json("post", "/environment/open")
    assert wait_for_environment_to_be_created(10), "Timeout"

    request_to_json("post", "/queue/start")
    ttime.sleep(2)

    status = request_to_json("get", "/status")
    assert status["manager_state"] == "executing_queue"
    run_list_uid = status["run_list_uid"]
    assert isinstance(run_list_uid, str)

    req = "/re/runs" + suffix
    resp2 = request_to_json("get", req)
    assert resp2["success"] is True
    assert len(resp2["run_list"]) == expected_n_items
    assert resp2["run_list_uid"] == run_list_uid

    assert wait_for_manager_state_idle(30), "Timeout"


_sample_trivial_plan1 = """
def trivial_plan_for_unit_test():
    '''
    Trivial plan for unit test.
    '''
    yield from scan([det1, det2], motor, -1, 1, 10)
"""


def test_http_server_reload_permissions(re_manager_pc_copy, fastapi_server, tmp_path):  # noqa F811
    """
    Tests for ``/permissions/reload`` API.
    """
    pc_path = copy_default_profile_collection(tmp_path)
    append_code_to_last_startup_file(pc_path, additional_code=_sample_trivial_plan1)

    # Generate the new list of allowed plans and devices and reload them
    gen_list_of_plans_and_devices(startup_dir=pc_path, file_dir=pc_path, overwrite=True)

    plan = {"item": {"name": "trivial_plan_for_unit_test", "item_type": "plan"}}

    # Attempt to add the plan to the queue. The request is supposed to fail, because
    #   the initially loaded profile collection does not contain the plan.
    resp1 = request_to_json("post", "/queue/item/add", json=plan)
    assert resp1["success"] is False, str(resp1)

    # Reload profile collection
    resp2 = request_to_json("post", "/permissions/reload")
    assert resp2["success"] is True, str(resp2)

    # Attempt to add the plan to the queue. It should be successful now.
    resp3 = request_to_json("post", "/queue/item/add", json=plan)
    assert resp3["success"] is True, str(resp3)
    assert resp3["qsize"] == 1, str(resp3)

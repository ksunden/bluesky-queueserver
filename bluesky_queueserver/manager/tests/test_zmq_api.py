import pytest
import os
import time as ttime
import asyncio
from copy import deepcopy

from bluesky_queueserver.manager.profile_ops import (
    get_default_startup_dir,
    load_allowed_plans_and_devices,
    gen_list_of_plans_and_devices,
)

from bluesky_queueserver.manager.plan_queue_ops import PlanQueueOperations

from ..comms import (
    zmq_single_request,
    ZMQCommSendThreads,
    ZMQCommSendAsync,
    CommTimeoutError,
    generate_new_zmq_key_pair,
)

from ._common import (
    zmq_secure_request,
    wait_for_condition,
    condition_environment_created,
    condition_queue_processing_finished,
    condition_manager_paused,
    get_queue_state,
    condition_environment_closed,
    condition_manager_idle,
    copy_default_profile_collection,
    append_code_to_last_startup_file,
    set_qserver_zmq_public_key,
)
from ._common import re_manager, re_manager_pc_copy, re_manager_cmd, db_catalog  # noqa: F401

# Plans used in most of the tests: '_plan1' and '_plan2' are quickly executed '_plan3' runs for 5 seconds.
_plan1 = {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"}
_plan2 = {"name": "scan", "args": [["det1", "det2"], "motor", -1, 1, 10], "item_type": "plan"}
_plan3 = {"name": "count", "args": [["det1", "det2"]], "kwargs": {"num": 5, "delay": 1}, "item_type": "plan"}
_instruction_stop = {"name": "queue_stop", "item_type": "instruction"}

# User name and user group name used throughout most of the tests.
_user, _user_group = "Testing Script", "admin"

_existing_plans_and_devices_fln = "existing_plans_and_devices.yaml"
_user_group_permissions_fln = "user_group_permissions.yaml"


# =======================================================================================
#                   Thread-based ZMQ API - ZMQCommSendThreads


def test_zmq_api_thread_based(re_manager):  # noqa F811
    """
    Communicate with the server using the thread-based API. The purpose of the test is to make
    sure that the API is compatible with the client. It is sufficient to test only
    the blocking call, since it is still using callbacks mechanism.
    """
    client = ZMQCommSendThreads()

    resp1 = client.send_message(
        method="queue_item_add", params={"item": _plan1, "user": _user, "user_group": _user_group}
    )
    assert resp1["success"] is True, str(resp1)
    assert resp1["qsize"] == 1
    assert resp1["item"]["item_type"] == _plan1["item_type"]
    assert resp1["item"]["name"] == _plan1["name"]
    assert resp1["item"]["args"] == _plan1["args"]
    assert resp1["item"]["user"] == _user
    assert resp1["item"]["user_group"] == _user_group
    assert "item_uid" in resp1["item"]

    resp2 = client.send_message(method="queue_get")
    assert resp2["items"] != []
    assert len(resp2["items"]) == 1
    assert resp2["items"][0] == resp1["item"]
    assert resp2["running_item"] == {}

    with pytest.raises(CommTimeoutError, match="timeout occurred"):
        client.send_message(method="manager_kill")

    # Wait until the manager is restarted
    ttime.sleep(6)

    resp3 = client.send_message(method="status")
    assert resp3["manager_state"] == "idle"
    assert resp3["items_in_queue"] == 1
    assert resp3["items_in_history"] == 0


# =======================================================================================
#                   Thread-based ZMQ API - ZMQCommSendThreads


def test_zmq_api_asyncio_based(re_manager):  # noqa F811
    """
    Communicate with the server using asyncio-based API. The purpose of the test is make
    sure that the API is compatible with the client.
    """

    async def testing():
        client = ZMQCommSendAsync()

        resp1 = await client.send_message(
            method="queue_item_add", params={"item": _plan1, "user": _user, "user_group": _user_group}
        )
        assert resp1["success"] is True, str(resp1)
        assert resp1["qsize"] == 1
        assert resp1["item"]["item_type"] == _plan1["item_type"]
        assert resp1["item"]["name"] == _plan1["name"]
        assert resp1["item"]["args"] == _plan1["args"]
        assert resp1["item"]["user"] == _user
        assert resp1["item"]["user_group"] == _user_group
        assert "item_uid" in resp1["item"]

        resp2 = await client.send_message(method="queue_get")
        assert resp2["items"] != []
        assert len(resp2["items"]) == 1
        assert resp2["items"][0] == resp1["item"]
        assert resp2["running_item"] == {}

        with pytest.raises(CommTimeoutError, match="timeout occurred"):
            await client.send_message(method="manager_kill")

        # Wait until the manager is restarted
        await asyncio.sleep(6)

        resp3 = await client.send_message(method="status")
        assert resp3["manager_state"] == "idle"
        assert resp3["items_in_queue"] == 1
        assert resp3["items_in_history"] == 0

    asyncio.run(testing())


# =======================================================================================
#                   Methods 'ping' (currently returning status), "status"

# fmt: off
@pytest.mark.parametrize("api_name", ["ping", "status"])
# fmt: on
def test_zmq_api_ping_status(re_manager, api_name):  # noqa F811
    resp, _ = zmq_single_request(api_name)
    assert resp["msg"] == "RE Manager"
    assert resp["manager_state"] == "idle"
    assert resp["items_in_queue"] == 0
    assert resp["running_item_uid"] is None
    assert resp["worker_environment_exists"] is False
    assert bool(resp["plan_queue_uid"])
    assert isinstance(resp["plan_queue_uid"], str), type(resp["plan_queue_uid"])
    assert bool(resp["plan_history_uid"])
    assert isinstance(resp["plan_history_uid"], str), type(resp["plan_history_uid"])


# =======================================================================================
#                   Methods 'environment_open', 'environment_close'


def test_zmq_api_environment_open_close_1(re_manager):  # noqa F811
    """
    Basic test for `environment_open` and `environment_close` methods.
    """
    resp1, _ = zmq_single_request("environment_open")
    assert resp1["success"] is True
    assert resp1["msg"] == ""

    assert wait_for_condition(time=3, condition=condition_environment_created)

    resp2, _ = zmq_single_request("environment_close")
    assert resp2["success"] is True
    assert resp2["msg"] == ""

    assert wait_for_condition(time=3, condition=condition_environment_closed)


def test_zmq_api_environment_open_close_2(re_manager):  # noqa F811
    """
    Test for `environment_open` and `environment_close` methods.
    Opening/closing the environment while it is being opened/closed.
    Opening the environment that already exists.
    Closing the environment that does not exist.
    """
    resp1a, _ = zmq_single_request("environment_open")
    assert resp1a["success"] is True
    # Attempt to open the environment before the previous operation is completed
    resp1b, _ = zmq_single_request("environment_open")
    assert resp1b["success"] is False
    assert "in the process of creating the RE Worker environment" in resp1b["msg"]

    assert wait_for_condition(time=3, condition=condition_environment_created)

    # Attempt to open the environment while it already exists
    resp2, _ = zmq_single_request("environment_open")
    assert resp2["success"] is False
    assert "RE Worker environment already exists" in resp2["msg"]

    resp3a, _ = zmq_single_request("environment_close")
    assert resp3a["success"] is True
    # The environment is being closed.
    resp3b, _ = zmq_single_request("environment_close")
    assert resp3b["success"] is False
    assert "in the process of closing the RE Worker environment" in resp3b["msg"]

    assert wait_for_condition(time=3, condition=condition_environment_closed)

    # The environment is already closed.
    resp4, _ = zmq_single_request("environment_close")
    assert resp4["success"] is False
    assert "RE Worker environment does not exist" in resp4["msg"]


def test_zmq_api_environment_open_close_3(re_manager):  # noqa F811
    """
    Test for `environment_open` and `environment_close` methods.
    Closing the environment while a plan is running.
    """
    resp1, _ = zmq_single_request("environment_open")
    assert resp1["success"] is True
    assert resp1["msg"] == ""

    assert wait_for_condition(time=3, condition=condition_environment_created)

    # Start a plan
    resp2, _ = zmq_single_request("queue_item_add", {"item": _plan3, "user": _user, "user_group": _user_group})
    assert resp2["success"] is True
    resp3, _ = zmq_single_request("queue_start")
    assert resp3["success"] is True

    # Try to close the environment while the plan is running
    resp4, _ = zmq_single_request("environment_close")
    assert resp4["success"] is False
    assert "Queue execution is in progress" in resp4["msg"]

    assert wait_for_condition(time=20, condition=condition_queue_processing_finished)

    resp2, _ = zmq_single_request("environment_close")
    assert resp2["success"] is True
    assert resp2["msg"] == ""

    assert wait_for_condition(time=3, condition=condition_environment_closed)


# =======================================================================================
#                             Method 'queue_item_add'


def test_zmq_api_queue_item_add_1(re_manager):  # noqa F811
    """
    Basic test for `queue_item_add` method.
    """
    status0 = get_queue_state()

    resp1, _ = zmq_single_request("queue_item_add", {"item": _plan1, "user": _user, "user_group": _user_group})
    assert resp1["success"] is True
    assert resp1["qsize"] == 1
    assert resp1["item"]["name"] == _plan1["name"]
    assert resp1["item"]["args"] == _plan1["args"]
    assert resp1["item"]["user"] == _user
    assert resp1["item"]["user_group"] == _user_group
    assert "item_uid" in resp1["item"]

    status1 = get_queue_state()
    assert status1["plan_queue_uid"] != status0["plan_queue_uid"]
    assert status1["plan_history_uid"] == status0["plan_history_uid"]

    resp2, _ = zmq_single_request("queue_get")
    assert resp2["items"] != []
    assert len(resp2["items"]) == 1
    assert resp2["items"][0] == resp1["item"]
    assert resp2["running_item"] == {}
    assert resp2["plan_queue_uid"] == status1["plan_queue_uid"]


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
def test_zmq_api_queue_item_add_2(re_manager, pos, pos_result, success):  # noqa F811

    plan1 = {"name": "count", "args": [["det1"]], "item_type": "plan"}
    plan2 = {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"}

    # Create the queue with 2 entries
    params1 = {"item": plan1, "user": _user, "user_group": _user_group}
    resp0a, _ = zmq_single_request("queue_item_add", params1)
    assert resp0a["success"] is True
    resp0b, _ = zmq_single_request("queue_item_add", params1)
    assert resp0b["success"] is True

    # Add another entry at the specified position
    params2 = {"item": plan2, "user": _user, "user_group": _user_group}
    if pos is not None:
        params2.update({"pos": pos})

    resp1, _ = zmq_single_request("queue_item_add", params2)

    assert resp1["success"] is success
    assert resp1["qsize"] == (3 if success else None)
    assert resp1["item"]["item_type"] == "plan"
    assert resp1["item"]["name"] == "count"
    assert resp1["item"]["args"] == plan2["args"]
    assert resp1["item"]["user"] == _user
    assert resp1["item"]["user_group"] == _user_group
    assert "item_uid" in resp1["item"]

    resp2, _ = zmq_single_request("queue_get")

    assert len(resp2["items"]) == (3 if success else 2)
    assert resp2["running_item"] == {}

    if success:
        assert resp2["items"][pos_result]["args"] == plan2["args"]


def test_zmq_api_queue_item_add_3(re_manager):  # noqa F811
    plan1 = {"name": "count", "args": [["det1"]], "item_type": "plan"}
    plan2 = {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"}
    plan3 = {"name": "count", "args": [["det2"]], "item_type": "plan"}

    params = {"item": plan1, "user": _user, "user_group": _user_group}
    resp0a, _ = zmq_single_request("queue_item_add", params)
    assert resp0a["success"] is True
    params = {"item": plan2, "user": _user, "user_group": _user_group}
    resp0b, _ = zmq_single_request("queue_item_add", params)
    assert resp0b["success"] is True

    base_plans = zmq_single_request("queue_get")[0]["items"]

    params = {"item": plan3, "after_uid": base_plans[0]["item_uid"], "user": _user, "user_group": _user_group}
    resp1, _ = zmq_single_request("queue_item_add", params)
    assert resp1["success"] is True
    assert resp1["qsize"] == 3
    uid1 = resp1["item"]["item_uid"]
    resp1a, _ = zmq_single_request("queue_get")
    assert len(resp1a["items"]) == 3
    assert resp1a["items"][1]["item_uid"] == uid1
    resp1b, _ = zmq_single_request("queue_item_remove", {"uid": uid1})
    assert resp1b["success"] is True

    params = {"item": plan3, "before_uid": base_plans[1]["item_uid"], "user": _user, "user_group": _user_group}
    resp2, _ = zmq_single_request("queue_item_add", params)
    assert resp2["success"] is True
    uid2 = resp2["item"]["item_uid"]
    resp2a, _ = zmq_single_request("queue_get")
    assert len(resp2a["items"]) == 3
    assert resp2a["items"][1]["item_uid"] == uid2
    resp2b, _ = zmq_single_request("queue_item_remove", {"uid": uid2})
    assert resp2b["success"] is True

    # Non-existing uid
    params = {"item": plan3, "before_uid": "non-existing-uid", "user": _user, "user_group": _user_group}
    resp2, _ = zmq_single_request("queue_item_add", params)
    assert resp2["success"] is False
    assert "is not in the queue" in resp2["msg"]

    # Ambiguous parameters
    params = {"item": plan3, "pos": 1, "before_uid": uid2, "user": _user, "user_group": _user_group}
    resp2, _ = zmq_single_request("queue_item_add", params)
    assert resp2["success"] is False
    assert "Ambiguous parameters" in resp2["msg"]

    # Ambiguous parameters
    params = {"item": plan3, "before_uid": uid2, "after_uid": uid2, "user": _user, "user_group": _user_group}
    resp2, _ = zmq_single_request("queue_item_add", params)
    assert resp2["success"] is False
    assert "Ambiguous parameters" in resp2["msg"]


def test_zmq_api_queue_item_add_4(re_manager):  # noqa F811
    """
    Try inserting plans before and after the running plan
    """
    params = {"item": _plan3, "user": _user, "user_group": _user_group}
    resp0a, _ = zmq_single_request("queue_item_add", params)
    assert resp0a["success"] is True
    params = {"item": _plan3, "user": _user, "user_group": _user_group}
    resp0b, _ = zmq_single_request("queue_item_add", params)
    assert resp0b["success"] is True

    base_plans = zmq_single_request("queue_get")[0]["items"]
    uid = base_plans[0]["item_uid"]

    # Start the first plan (this removes it from the queue)
    #   Also the rest of the operations will be performed on a running queue.
    resp1, _ = zmq_single_request("environment_open")
    assert resp1["success"] is True
    assert wait_for_condition(
        time=3, condition=condition_environment_created
    ), "Timeout while waiting for environment to be opened"

    resp2, _ = zmq_single_request("queue_start")
    assert resp2["success"] is True

    ttime.sleep(1)

    # Try to insert a plan before the running plan
    params = {"item": _plan3, "before_uid": uid, "user": _user, "user_group": _user_group}
    resp3, _ = zmq_single_request("queue_item_add", params)
    assert resp3["success"] is False
    assert "Can not insert a plan in the queue before a currently running plan" in resp3["msg"]

    # Insert the plan after the running plan
    params = {"item": _plan3, "after_uid": uid, "user": _user, "user_group": _user_group}
    resp4, _ = zmq_single_request("queue_item_add", params)
    assert resp4["success"] is True

    assert wait_for_condition(
        time=20, condition=condition_queue_processing_finished
    ), "Timeout while waiting for environment to be opened"

    state = get_queue_state()
    assert state["items_in_queue"] == 0
    assert state["items_in_history"] == 3

    # Close the environment
    resp5, _ = zmq_single_request("environment_close")
    assert resp5["success"] is True
    assert wait_for_condition(time=5, condition=condition_environment_closed)


def test_zmq_api_queue_item_add_5(re_manager):  # noqa: F811
    """
    Make sure that the new plan UID is generated when the plan is added
    """
    plan1 = {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"}

    # Set plan UID. This UID is expected to be replaced when the plan is added
    plan1["item_uid"] = PlanQueueOperations.new_item_uid()

    params1 = {"item": plan1, "user": _user, "user_group": _user_group}
    resp1, _ = zmq_single_request("queue_item_add", params1)
    assert resp1["success"] is True
    assert resp1["msg"] == ""
    assert resp1["item"]["item_uid"] != plan1["item_uid"]


def test_zmq_api_queue_item_add_6(re_manager):  # noqa: F811
    """
    Add instruction ('queue_stop') to the queue.
    """

    params1a = {"item": _plan1, "user": _user, "user_group": _user_group}
    resp1a, _ = zmq_single_request("queue_item_add", params1a)
    assert resp1a["success"] is True, f"resp={resp1a}"

    params1 = {"item": _instruction_stop, "user": _user, "user_group": _user_group}
    resp1, _ = zmq_single_request("queue_item_add", params1)
    assert resp1["success"] is True, f"resp={resp1}"
    assert resp1["msg"] == ""
    assert resp1["item"]["name"] == "queue_stop"

    params1c = {"item": _plan2, "user": _user, "user_group": _user_group}
    resp1c, _ = zmq_single_request("queue_item_add", params1c)
    assert resp1c["success"] is True, f"resp={resp1c}"

    resp2, _ = zmq_single_request("queue_get")
    assert len(resp2["items"]) == 3
    assert resp2["items"][0]["item_type"] == "plan"
    assert resp2["items"][1]["item_type"] == "instruction"
    assert resp2["items"][2]["item_type"] == "plan"


# fmt: off
@pytest.mark.parametrize("meta_param, meta_saved", [
    # 'meta' is dictionary, all keys are saved
    ({"test_key": "test_value"}, {"test_key": "test_value"}),
    # 'meta' - array with two elements. Merging dictionaries with distinct keys.
    ([{"test_key1": 10}, {"test_key2": 20}], {"test_key1": 10, "test_key2": 20}),
    # ' meta' - array. Merging dictionaries with identical keys.
    ([{"test_key": 10}, {"test_key": 20}], {"test_key": 10}),
])
# fmt: on
def test_zmq_api_queue_item_add_7(db_catalog, re_manager_cmd, meta_param, meta_saved):  # noqa: F811
    """
    Add plan with metadata.
    """
    re_manager_cmd(["--databroker-config", db_catalog["catalog_name"]])
    cat = db_catalog["catalog"]

    # Plan
    plan = deepcopy(_plan2)
    plan["meta"] = meta_param
    params1 = {"item": plan, "user": _user, "user_group": _user_group}
    resp1, _ = zmq_single_request("queue_item_add", params1)
    assert resp1["success"] is True, f"resp={resp1}"

    resp2, _ = zmq_single_request("status")
    assert resp2["items_in_queue"] == 1
    assert resp2["items_in_history"] == 0

    # Open the environment.
    resp3, _ = zmq_single_request("environment_open")
    assert resp3["success"] is True
    assert wait_for_condition(time=10, condition=condition_environment_created)

    resp4, _ = zmq_single_request("queue_start")
    assert resp4["success"] is True

    assert wait_for_condition(time=5, condition=condition_manager_idle)

    resp5, _ = zmq_single_request("status")
    assert resp5["items_in_queue"] == 0
    assert resp5["items_in_history"] == 1

    resp6, _ = zmq_single_request("history_get")
    history = resp6["items"]
    assert len(history) == 1

    # Check if metadata was recorded in the start document.
    uid = history[-1]["result"]["run_uids"][0]
    start_doc = cat[uid].metadata["start"]
    for key in meta_saved:
        assert key in start_doc, str(start_doc)
        assert meta_saved[key] == start_doc[key], str(start_doc)

    # Close the environment.
    resp7, _ = zmq_single_request("environment_close")
    assert resp7["success"] is True, f"resp={resp7}"
    assert wait_for_condition(time=5, condition=condition_environment_closed)


def test_zmq_api_queue_item_add_8_fail(re_manager):  # noqa F811

    # Unknown plan name
    plan1 = {"name": "count_test", "args": [["det1", "det2"]], "item_type": "plan"}
    params1 = {"item": plan1, "user": _user, "user_group": _user_group}
    resp1, _ = zmq_single_request("queue_item_add", params1)
    assert resp1["success"] is False
    assert "Plan 'count_test' is not in the list of allowed plans" in resp1["msg"]

    # Unknown kwarg
    plan2 = {"name": "count", "args": [["det1", "det2"]], "kwargs": {"abc": 10}, "item_type": "plan"}
    params2 = {"item": plan2, "user": _user, "user_group": _user_group}
    resp2, _ = zmq_single_request("queue_item_add", params2)
    assert resp2["success"] is False
    assert (
        "Failed to add an item: Plan validation failed: got an unexpected keyword argument 'abc'" in resp2["msg"]
    )

    # User name is not specified
    params3 = {"item": plan2, "user_group": _user_group}
    resp3, _ = zmq_single_request("queue_item_add", params3)
    assert resp3["success"] is False
    assert "user name is not specified" in resp3["msg"]

    # User group is not specified
    params4 = {"item": plan2, "user": _user}
    resp4, _ = zmq_single_request("queue_item_add", params4)
    assert resp4["success"] is False
    assert "user group is not specified" in resp4["msg"]

    # Unknown user group
    params5 = {"item": plan2, "user": _user, "user_group": "no_such_group"}
    resp5, _ = zmq_single_request("queue_item_add", params5)
    assert resp5["success"] is False
    assert "Unknown user group: 'no_such_group'" in resp5["msg"]

    # Missing item parameters
    params6 = {"user": _user, "user_group": _user_group}
    resp6, _ = zmq_single_request("queue_item_add", params6)
    assert resp6["success"] is False
    assert resp6["item"] is None
    assert "Incorrect request format: request contains no item info" in resp6["msg"]

    # Incorrect type of the item parameter (must be dict)
    params6a = {"item": [], "user": _user, "user_group": _user_group}
    resp6a, _ = zmq_single_request("queue_item_add", params6a)
    assert resp6a["success"] is False
    assert resp6a["item"] == []
    assert "item parameter must have type 'dict'" in resp6a["msg"]

    # Unsupported item type
    plan7 = {"name": "count_test", "args": [["det1", "det2"]], "item_type": "unsupported"}
    params7 = {"item": plan7, "user": _user, "user_group": _user_group}
    resp7, _ = zmq_single_request("queue_item_add", params7)
    assert resp7["success"] is False
    assert resp7["item"] == plan7
    assert "Incorrect request format: unsupported 'item_type' value 'unsupported'" in resp7["msg"]

    # Valid plan
    plan8 = {"name": "count", "args": [["det1", "det2"]], "item_type": "plan"}
    params8 = {"item": plan8, "user": _user, "user_group": _user_group}
    resp8, _ = zmq_single_request("queue_item_add", params8)
    assert resp8["success"] is True
    assert resp8["qsize"] == 1
    assert resp8["item"]["name"] == "count"
    assert resp8["item"]["args"] == [["det1", "det2"]]
    assert resp8["item"]["user"] == _user
    assert resp8["item"]["user_group"] == _user_group
    assert "item_uid" in resp8["item"]

    resp9, _ = zmq_single_request("queue_get")
    assert resp9["items"] != []
    assert len(resp9["items"]) == 1
    assert resp9["items"][0] == resp8["item"]
    assert resp9["running_item"] == {}


# =======================================================================================
#                          Method 'queue_item_add_batch'


def test_zmq_api_queue_item_add_batch_1(re_manager):  # noqa: F811
    """
    Basic test for ``queue_item_add_batch`` API.
    """
    items = [_plan1, _plan2, _instruction_stop, _plan3]

    params = {"items": items, "user": _user, "user_group": _user_group}
    resp1a, _ = zmq_single_request("queue_item_add_batch", params)
    assert resp1a["success"] is True, f"resp={resp1a}"
    assert resp1a["msg"] == ""
    assert resp1a["qsize"] == 4
    item_list = resp1a["items"]
    item_results = resp1a["results"]
    assert len(item_list) == len(items)
    assert len(item_results) == len(items)

    for n, item in enumerate(items):
        item_res, res = item_list[n], item_results[n]
        assert res["success"] is True, str(item)
        assert res["msg"] == "", str(item)

        assert "name" in item_res, str(item_res)
        assert item_res["name"] == item["name"]
        assert isinstance(item_res["item_uid"], str)
        assert item_res["item_uid"]

        if "args" in item:
            assert item_res["args"] == item["args"]
        else:
            assert "args" not in item_res
        if "kwargs" in item:
            assert item_res["kwargs"] == item["kwargs"]
        else:
            assert "kwargs" not in item_res

    state = get_queue_state()
    assert state["items_in_queue"] == 4
    assert state["items_in_history"] == 0

    resp2, _ = zmq_single_request("environment_open")
    assert resp2["success"] is True
    assert wait_for_condition(time=3, condition=condition_environment_created)

    resp3, _ = zmq_single_request("queue_start")
    assert resp3["success"] is True
    assert wait_for_condition(time=30, condition=condition_manager_idle)

    state = get_queue_state()
    assert state["items_in_queue"] == 1
    assert state["items_in_history"] == 2

    resp4, _ = zmq_single_request("queue_start")
    assert resp4["success"] is True
    assert wait_for_condition(time=10, condition=condition_queue_processing_finished)

    state = get_queue_state()
    assert state["items_in_queue"] == 0
    assert state["items_in_history"] == 3

    resp5, _ = zmq_single_request("environment_close")
    assert resp5["success"] is True
    assert wait_for_condition(time=5, condition=condition_manager_idle)


def test_zmq_api_queue_item_add_batch_2(re_manager):  # noqa: F811
    """
    Test for ``queue_item_add_batch`` API. Attempt to add a batch that contains invalid plans.
    Make sure that the invalid plans are detected, correct error messages are returned and the
    plans from the batch are not added to the queue.
    """
    _plan2_corrupt = _plan2.copy()
    _plan2_corrupt["name"] = "nonexisting_name"
    items = [_plan1, _plan2_corrupt, _instruction_stop, {}, _plan3]
    success_expected = [True, False, True, False, True]
    msg_expected = ["", "is not in the list of allowed plans", "", "'item_type' key is not found", ""]

    params = {"items": items, "user": _user, "user_group": _user_group}
    resp1a, _ = zmq_single_request("queue_item_add_batch", params)
    assert resp1a["success"] is False, f"resp={resp1a}"
    assert resp1a["msg"] == "Failed to add all items: validation of 2 out of 5 submitted items failed"
    item_list = resp1a["items"]
    item_results = resp1a["results"]
    assert len(item_list) == len(items)
    assert len(item_results) == len(items)

    for n, item in enumerate(items):
        item_res, res = item_list[n], item_results[n]
        scs, msg = success_expected[n], msg_expected[n]
        assert res["success"] == scs, str(item)
        if not msg:
            assert res["msg"] == "", str(item)
        else:
            assert msg in res["msg"], str(item)

        if item:  # We should attempt to access elements of an item, which is {}
            assert "name" in item_res, str(item_res)
            assert item_res["name"] == item["name"]
            if scs:
                assert isinstance(item_res["item_uid"], str)
                assert item_res["item_uid"]
            else:
                assert "item_uid" not in item_res

            if "args" in item:
                assert item_res["args"] == item["args"]
            else:
                assert "args" not in item_res
            if "kwargs" in item:
                assert item_res["kwargs"] == item["kwargs"]
            else:
                assert "kwargs" not in item_res
        else:
            assert item_res == {}

    state = get_queue_state()
    assert state["items_in_queue"] == 0
    assert state["items_in_history"] == 0


def test_zmq_api_queue_item_add_batch_3(re_manager):  # noqa: F811
    """
    Test for ``queue_item_add_batch`` API. Add an empty batch. The operation should return success.
    """
    items = []

    params = {"items": items, "user": _user, "user_group": _user_group}
    resp1a, _ = zmq_single_request("queue_item_add_batch", params)
    assert resp1a["success"] is True, f"resp={resp1a}"
    assert resp1a["msg"] == ""
    assert resp1a["items"] == []
    assert resp1a["results"] == []

    state = get_queue_state()
    assert state["items_in_queue"] == 0
    assert state["items_in_history"] == 0


# =======================================================================================
#                            Method 'queue_item_update'

# fmt: on
@pytest.mark.parametrize("replace", [None, False, True])
# fmt: off
def test_zmq_api_queue_item_update_1(re_manager, replace):  # noqa F811
    """
    Basic test for `queue_item_update` method.
    """

    resp1, _ = zmq_single_request("queue_item_add", {"item": _plan1, "user": _user, "user_group": _user_group})
    assert resp1["success"] is True
    assert resp1["qsize"] == 1
    assert resp1["item"]["name"] == _plan1["name"]
    assert resp1["item"]["args"] == _plan1["args"]
    assert resp1["item"]["user"] == _user
    assert resp1["item"]["user_group"] == _user_group
    assert "item_uid" in resp1["item"]

    plan = resp1["item"]
    uid = plan["item_uid"]

    plan_changed = plan.copy()
    plan_new_args = [["det1"]]
    plan_changed["args"] = plan_new_args

    user_replaced = "Different User"
    params = {"item": plan_changed, "user": user_replaced, "user_group": _user_group}
    if replace is not None:
        params["replace"] = replace

    status1 = get_queue_state()

    resp2, _ = zmq_single_request("queue_item_update", params)
    assert resp2["success"] is True
    assert resp2["qsize"] == 1
    assert resp2["item"]["name"] == _plan1["name"]
    assert resp2["item"]["args"] == plan_new_args
    assert resp2["item"]["user"] == user_replaced
    assert resp2["item"]["user_group"] == _user_group
    assert "item_uid" in resp2["item"]
    if replace:
        assert resp2["item"]["item_uid"] != uid
    else:
        assert resp2["item"]["item_uid"] == uid

    status2 = get_queue_state()
    assert status2["plan_queue_uid"] != status1["plan_queue_uid"]
    assert status2["plan_history_uid"] == status1["plan_history_uid"]

    resp3, _ = zmq_single_request("queue_get")
    assert resp3["items"] != []
    assert len(resp3["items"]) == 1
    assert resp3["items"][0] == resp2["item"]
    assert resp3["running_item"] == {}
    assert resp3["plan_queue_uid"] == status2["plan_queue_uid"]


# fmt: on
@pytest.mark.parametrize("replace", [None, False, True])
# fmt: off
def test_zmq_api_queue_item_update_2_fail(re_manager, replace):  # noqa F811
    """
    Failing cases for `queue_item_update`: submitted item UID does not match any UID in the queue.
    """
    resp1, _ = zmq_single_request("queue_item_add", {"item": _plan1, "user": _user, "user_group": _user_group})
    assert resp1["success"] is True
    assert resp1["qsize"] == 1
    assert resp1["item"]["name"] == _plan1["name"]
    assert resp1["item"]["args"] == _plan1["args"]
    assert resp1["item"]["user"] == _user
    assert resp1["item"]["user_group"] == _user_group
    assert "item_uid" in resp1["item"]

    plan = resp1["item"]

    plan_changed = plan.copy()
    plan_changed["args"] = [["det1"]]
    plan_changed["item_uid"] = "incorrect_uid"

    user_replaced = "Different User"
    params = {"item": plan_changed, "user": user_replaced, "user_group": _user_group}
    if replace is not None:
        params["replace"] = replace

    resp2, _ = zmq_single_request("queue_item_update", params)
    assert resp2["success"] is False
    assert resp2["msg"] == "Failed to add an item: Failed to replace item: " \
                           "Item with UID 'incorrect_uid' is not in the queue"

    resp3, _ = zmq_single_request("queue_get")
    assert resp3["items"] != []
    assert len(resp3["items"]) == 1
    assert resp3["items"][0] == plan
    assert resp3["running_item"] == {}


# fmt: on
@pytest.mark.parametrize("replace", [None, False, True])
# fmt: off
def test_zmq_api_queue_item_update_3_fail(re_manager, replace):  # noqa F811
    """
    Failing cases for `queue_item_update`: submitted item UID does not match any UID in the queue
    (the case of empty queue - expected to work the same as for non-empty queue)
    """
    resp1, _ = zmq_single_request("queue_get")
    assert resp1["items"] == []
    assert resp1["running_item"] == {}

    plan_changed = _plan1
    plan_changed["item_uid"] = "incorrect_uid"

    user_replaced = "Different User"
    params = {"item": plan_changed, "user": user_replaced, "user_group": _user_group}
    if replace is not None:
        params["replace"] = replace

    resp2, _ = zmq_single_request("queue_item_update", params)
    assert resp2["success"] is False
    assert resp2["msg"] == "Failed to add an item: Failed to replace item: " \
                           "Item with UID 'incorrect_uid' is not in the queue"

    resp3, _ = zmq_single_request("queue_get")
    assert resp3["items"] == []
    assert resp3["running_item"] == {}


def test_zmq_api_queue_item_update_4_fail(re_manager):  # noqa F811
    """
    Failing cases for ``queue_item_update`` API: verify that it works identically to 'queue_item_add' for
    all failing cases.
    """

    resp1, _ = zmq_single_request("queue_item_add", {"item": _plan1, "user": _user, "user_group": _user_group})
    assert resp1["success"] is True
    assert resp1["qsize"] == 1
    assert resp1["item"]["name"] == _plan1["name"]
    assert resp1["item"]["args"] == _plan1["args"]
    assert resp1["item"]["user"] == _user
    assert resp1["item"]["user_group"] == _user_group
    assert "item_uid" in resp1["item"]

    plan_to_update = resp1["item"].copy()

    # Unknown plan name
    plan2 = plan_to_update.copy()
    plan2["name"] = "count_test"
    params2 = {"item": plan2, "user": _user, "user_group": _user_group}
    resp2, _ = zmq_single_request("queue_item_update", params2)
    assert resp2["success"] is False
    assert "Plan 'count_test' is not in the list of allowed plans" in resp2["msg"]

    # Unknown kwarg
    plan3 = plan_to_update.copy()
    plan3["kwargs"] = {"abc": 10}
    params3 = {"item": plan3, "user": _user, "user_group": _user_group}
    resp3, _ = zmq_single_request("queue_item_update", params3)
    assert resp3["success"] is False
    assert (
        "Failed to add an item: Plan validation failed: got an unexpected keyword argument 'abc'" in resp3["msg"]
    )

    # User name is not specified
    params4 = {"item": plan_to_update, "user_group": _user_group}
    resp4, _ = zmq_single_request("queue_item_update", params4)
    assert resp4["success"] is False
    assert "user name is not specified" in resp4["msg"]

    # User group is not specified
    params5 = {"item": plan_to_update, "user": _user}
    resp5, _ = zmq_single_request("queue_item_update", params5)
    assert resp5["success"] is False
    assert "user group is not specified" in resp5["msg"]

    # Unknown user group
    params6 = {"item": plan_to_update, "user": _user, "user_group": "no_such_group"}
    resp6, _ = zmq_single_request("queue_item_update", params6)
    assert resp6["success"] is False
    assert "Unknown user group: 'no_such_group'" in resp6["msg"]

    # Missing item parameters
    params7 = {"user": _user, "user_group": _user_group}
    resp7, _ = zmq_single_request("queue_item_update", params7)
    assert resp7["success"] is False
    assert resp7["item"] is None
    assert "Incorrect request format: request contains no item info" in resp7["msg"]

    # Incorrect type of the item parameter (must be dict)
    params8 = {"item": [], "user": _user, "user_group": _user_group}
    resp8, _ = zmq_single_request("queue_item_update", params8)
    assert resp8["success"] is False
    assert resp8["item"] == []
    assert "item parameter must have type 'dict'" in resp8["msg"]

    # Unsupported item type
    plan9 = plan_to_update.copy()
    plan9["item_type"] = "unsupported"
    params9 = {"item": plan9, "user": _user, "user_group": _user_group}
    resp9, _ = zmq_single_request("queue_item_update", params9)
    assert resp9["success"] is False
    assert resp9["item"] == plan9
    assert "Incorrect request format: unsupported 'item_type' value 'unsupported'" in resp9["msg"]

    # Valid plan
    plan10 = plan_to_update.copy()
    plan10["args"] = [["det1"]]
    params10 = {"item": plan10, "user": _user, "user_group": _user_group}
    resp10, _ = zmq_single_request("queue_item_update", params10)
    assert resp10["success"] is True
    assert resp10["qsize"] == 1
    assert resp10["item"]["name"] == "count"
    assert resp10["item"]["args"] == [["det1"]]
    assert resp10["item"]["user"] == _user
    assert resp10["item"]["user_group"] == _user_group
    assert "item_uid" in resp10["item"]
    assert resp10["item"]["item_uid"] == plan_to_update["item_uid"]

    resp11, _ = zmq_single_request("queue_get")
    assert resp11["items"] != []
    assert len(resp11["items"]) == 1
    assert resp11["items"][0] == resp10["item"]
    assert resp11["running_item"] == {}


# =======================================================================================
#                      Method 'plans_allowed', 'devices_allowed'


def test_zmq_api_plans_allowed_and_devices_allowed_1(re_manager):  # noqa F811
    """
    Basic calls to 'plans_allowed', 'devices_allowed' methods.
    """
    params = {"user_group": _user_group}
    resp1, _ = zmq_single_request("plans_allowed", params)
    assert resp1["success"] is True
    assert resp1["msg"] == ""
    assert isinstance(resp1["plans_allowed"], dict)
    assert len(resp1["plans_allowed"]) > 0
    resp2, _ = zmq_single_request("devices_allowed", params)
    assert resp2["success"] is True
    assert resp2["msg"] == ""
    assert isinstance(resp2["devices_allowed"], dict)
    assert len(resp2["devices_allowed"]) > 0


def test_zmq_api_plans_allowed_and_devices_allowed_2(re_manager):  # noqa F811
    """
    Test that group names are recognized correctly. The number of returned plans and
    devices is compared to the number of plans and devices loaded from the profile
    collection. The functions for loading files and generating lists are tested
    separately somewhere else.
    """

    pc_path = get_default_startup_dir()
    path_epd = os.path.join(pc_path, _existing_plans_and_devices_fln)
    path_up = os.path.join(pc_path, _user_group_permissions_fln)

    allowed_plans, allowed_devices = load_allowed_plans_and_devices(
        path_existing_plans_and_devices=path_epd, path_user_group_permissions=path_up
    )

    # Make sure that the user groups is the same. Otherwise it's a bug.
    assert set(allowed_plans.keys()) == set(allowed_devices.keys())

    group_info = {
        _: {"n_plans": len(allowed_plans[_]), "n_devices": len(allowed_devices[_])} for _ in allowed_plans.keys()
    }

    for group, info in group_info.items():
        params = {"user_group": group}
        resp1, _ = zmq_single_request("plans_allowed", params)
        resp2, _ = zmq_single_request("devices_allowed", params)
        allowed_plans = resp1["plans_allowed"]
        allowed_devices = resp2["devices_allowed"]
        assert len(allowed_plans) == info["n_plans"]
        assert len(allowed_devices) == info["n_devices"]


# fmt: off
@pytest.mark.parametrize("params, message", [
    ({}, "user group is not specified"),
    ({"user_group": "no_such_group"}, "Unknown user group: 'no_such_group'"),
])
# fmt: on
def test_zmq_api_plans_allowed_and_devices_allowed_3_fail(re_manager, params, message):  # noqa F811
    """
    Some failing cases for 'plans_allowed', 'devices_allowed' methods.
    """
    resp1, _ = zmq_single_request("plans_allowed", params)
    assert resp1["success"] is False
    assert message in resp1["msg"]
    assert isinstance(resp1["plans_allowed"], dict)
    assert len(resp1["plans_allowed"]) == 0
    resp2, _ = zmq_single_request("devices_allowed", params)
    assert resp1["success"] is False
    assert message in resp1["msg"]
    assert isinstance(resp2["devices_allowed"], dict)
    assert len(resp2["devices_allowed"]) == 0


# =======================================================================================
#                      Method 'queue_item_get', 'queue_item_remove'


def test_zmq_api_queue_item_get_remove_1(re_manager):  # noqa F811
    """
    Get and remove a plan from the back of the queue
    """
    plans = [_plan1, _plan2, _plan3]
    for plan in plans:
        resp0, _ = zmq_single_request("queue_item_add", {"item": plan, "user": _user, "user_group": _user_group})
        assert resp0["success"] is True

    status0 = get_queue_state()

    resp1, _ = zmq_single_request("queue_get")
    assert resp1["items"] != []
    assert len(resp1["items"]) == 3
    assert resp1["running_item"] == {}
    assert resp1["plan_queue_uid"] == status0["plan_queue_uid"]

    # Get the last plan from the queue
    resp2, _ = zmq_single_request("queue_item_get")
    assert resp2["success"] is True
    assert resp2["item"]["name"] == _plan3["name"]
    assert resp2["item"]["args"] == _plan3["args"]
    assert resp2["item"]["kwargs"] == _plan3["kwargs"]
    assert "item_uid" in resp2["item"]

    # Remove the last plan from the queue
    resp3, _ = zmq_single_request("queue_item_remove")
    assert resp3["success"] is True
    assert resp3["qsize"] == 2
    assert resp3["item"]["name"] == "count"
    assert resp3["item"]["args"] == [["det1", "det2"]]
    assert resp2["item"]["kwargs"] == _plan3["kwargs"]
    assert "item_uid" in resp3["item"]

    status1 = get_queue_state()
    assert status1["plan_queue_uid"] != status0["plan_queue_uid"]


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
def test_zmq_api_queue_item_get_remove_2(re_manager, pos, pos_result, success):  # noqa F811
    """
    Get and remove elements using element position in the queue.
    """

    plans = [
        {"item_uid": "one", "name": "count", "args": [["det1"]], "item_type": "plan"},
        {"item_uid": "two", "name": "count", "args": [["det2"]], "item_type": "plan"},
        {"item_uid": "three", "name": "count", "args": [["det1", "det2"]], "item_type": "plan"},
    ]
    for plan in plans:
        resp0, _ = zmq_single_request("queue_item_add", {"item": plan, "user": _user, "user_group": _user_group})
        assert resp0["success"] is True

    # Remove entry at the specified position
    params = {} if pos is None else {"pos": pos}

    # Testing 'queue_item_get'
    resp1, _ = zmq_single_request("queue_item_get", params)
    assert resp1["success"] is success
    if success:
        assert resp1["item"]["args"] == plans[pos_result]["args"]
        assert "item_uid" in resp1["item"]
        assert resp1["msg"] == ""
    else:
        assert resp1["item"] == {}
        assert "Failed to get an item" in resp1["msg"]

    # Testing 'queue_item_remove'
    resp2, _ = zmq_single_request("queue_item_remove", params)
    assert resp2["success"] is success
    assert resp2["qsize"] == (2 if success else None)
    if success:
        assert resp2["item"]["args"] == plans[pos_result]["args"]
        assert "item_uid" in resp2["item"]
        assert resp2["msg"] == ""
    else:
        assert resp2["item"] == {}
        assert "Failed to remove an item" in resp2["msg"]

    resp3, _ = zmq_single_request("queue_get")
    assert len(resp3["items"]) == (2 if success else 3)
    assert resp3["running_item"] == {}


def test_zmq_api_queue_item_get_remove_3(re_manager):  # noqa F811
    """
    Get and remove elements using plan UID. Successful and failing cases.
    """
    plans = [_plan3, _plan2, _plan1]
    for plan in plans:
        resp0, _ = zmq_single_request("queue_item_add", {"item": plan, "user": _user, "user_group": _user_group})
        assert resp0["success"] is True

    resp1, _ = zmq_single_request("queue_get")
    plans_in_queue = resp1["items"]
    assert len(plans_in_queue) == 3

    # Get and then remove plan 2 from the queue
    uid = plans_in_queue[1]["item_uid"]
    resp2a, _ = zmq_single_request("queue_item_get", {"uid": uid})
    assert resp2a["item"]["item_uid"] == plans_in_queue[1]["item_uid"]
    assert resp2a["item"]["name"] == plans_in_queue[1]["name"]
    assert resp2a["item"]["args"] == plans_in_queue[1]["args"]
    resp2b, _ = zmq_single_request("queue_item_remove", {"uid": uid})
    assert resp2b["item"]["item_uid"] == plans_in_queue[1]["item_uid"]
    assert resp2b["item"]["name"] == plans_in_queue[1]["name"]
    assert resp2b["item"]["args"] == plans_in_queue[1]["args"]

    # Start the first plan (this removes it from the queue)
    #   Also the rest of the operations will be performed on a running queue.
    resp3, _ = zmq_single_request("environment_open")
    assert resp3["success"] is True
    assert wait_for_condition(
        time=3, condition=condition_environment_created
    ), "Timeout while waiting for environment to be opened"

    resp4, _ = zmq_single_request("queue_start")
    assert resp4["success"] is True

    ttime.sleep(1)
    uid = plans_in_queue[0]["item_uid"]
    resp5a, _ = zmq_single_request("queue_item_get", {"uid": uid})
    assert resp5a["success"] is False
    assert "is currently running" in resp5a["msg"]
    resp5b, _ = zmq_single_request("queue_item_remove", {"uid": uid})
    assert resp5b["success"] is False
    assert "Can not remove an item which is currently running" in resp5b["msg"]

    uid = "nonexistent"
    resp6a, _ = zmq_single_request("queue_item_get", {"uid": uid})
    assert resp6a["success"] is False
    assert "not in the queue" in resp6a["msg"]
    resp6b, _ = zmq_single_request("queue_item_remove", {"uid": uid})
    assert resp6b["success"] is False
    assert "not in the queue" in resp6b["msg"]

    # Remove the last entry
    uid = plans_in_queue[2]["item_uid"]
    resp7a, _ = zmq_single_request("queue_item_get", {"uid": uid})
    assert resp7a["success"] is True
    resp7b, _ = zmq_single_request("queue_item_remove", {"uid": uid})
    assert resp7b["success"] is True

    assert wait_for_condition(
        time=10, condition=condition_queue_processing_finished
    ), "Timeout while waiting for environment to be opened"

    state = get_queue_state()
    assert state["items_in_queue"] == 0
    assert state["items_in_history"] == 1

    # Close the environment
    resp8, _ = zmq_single_request("environment_close")
    assert resp8["success"] is True
    assert wait_for_condition(time=5, condition=condition_environment_closed)


def test_zmq_api_queue_item_get_remove_4_failing(re_manager):  # noqa F811
    """
    Failing cases that are not tested in other places.
    """
    # Ambiguous parameters
    resp1, _ = zmq_single_request("queue_item_get", {"pos": 5, "uid": "some_uid"})
    assert resp1["success"] is False
    assert "Ambiguous parameters" in resp1["msg"]


# =======================================================================================
#                              Method `queue_item_move`

# fmt: off
@pytest.mark.parametrize("params, src, order, success, msg", [
    ({"pos": 1, "pos_dest": 1}, 1, [0, 1, 2], True, ""),
    ({"pos": 1, "pos_dest": 0}, 1, [1, 0, 2], True, ""),
    ({"pos": 1, "pos_dest": 2}, 1, [0, 2, 1], True, ""),
    ({"pos": "front", "pos_dest": "front"}, 0, [0, 1, 2], True, ""),
    ({"pos": "back", "pos_dest": "back"}, 2, [0, 1, 2], True, ""),
    ({"pos": "front", "pos_dest": "back"}, 0, [1, 2, 0], True, ""),
    ({"pos": "back", "pos_dest": "front"}, 2, [2, 0, 1], True, ""),
    ({"uid": 1, "pos_dest": 1}, 1, [0, 1, 2], True, ""),
    ({"uid": 1, "pos_dest": 0}, 1, [1, 0, 2], True, ""),
    ({"uid": 1, "pos_dest": 2}, 1, [0, 2, 1], True, ""),
    ({"uid": 1, "pos_dest": 1}, 1, [0, 1, 2], True, ""),
    ({"uid": 1, "pos_dest": "front"}, 1, [1, 0, 2], True, ""),
    ({"uid": 1, "pos_dest": "back"}, 1, [0, 2, 1], True, ""),
    ({"uid": 0, "pos_dest": "front"}, 0, [0, 1, 2], True, ""),
    ({"uid": 2, "pos_dest": "back"}, 2, [0, 1, 2], True, ""),
    ({"uid": 0, "before_uid": 0}, 0, [0, 1, 2], True, ""),
    ({"uid": 0, "after_uid": 0}, 0, [0, 1, 2], True, ""),
    ({"uid": 2, "before_uid": 2}, 2, [0, 1, 2], True, ""),
    ({"uid": 2, "after_uid": 2}, 2, [0, 1, 2], True, ""),
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
def test_zmq_api_move_plan_1(re_manager, params, src, order, success, msg):  # noqa: F811
    plans = [_plan1, _plan2, _plan3]
    for plan in plans:
        resp0, _ = zmq_single_request("queue_item_add", {"item": plan, "user": _user, "user_group": _user_group})
        assert resp0["success"] is True

    resp1, _ = zmq_single_request("queue_get")
    queue = resp1["items"]
    pq_uid = resp1["plan_queue_uid"]
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

    resp2, _ = zmq_single_request("queue_item_move", params)
    if success:
        assert resp2["success"] is True
        assert resp2["item"] == queue[src]
        assert resp2["qsize"] == len(plans)
        assert resp2["msg"] == ""

        # Compare the order of UIDs in the queue with the expected order
        item_uids_reordered = [item_uids[_] for _ in order]
        resp3, _ = zmq_single_request("queue_get")
        item_uids_from_queue = [_["item_uid"] for _ in resp3["items"]]

        assert item_uids_from_queue == item_uids_reordered

        status = get_queue_state()
        if order != [0, 1, 2]:
            # The queue actually changed, so UID is expected to change
            assert status["plan_queue_uid"] != pq_uid
        else:
            # The queue did not change, so UID is expected to remain the same
            assert status["plan_queue_uid"] == pq_uid

    else:
        assert resp2["success"] is False
        assert msg in resp2["msg"]

        status = get_queue_state()
        # Queue did not change, so UID should remain the same
        assert status["plan_queue_uid"] == pq_uid


# =======================================================================================
#                              Method `re_runs`


_sample_multirun_plan1 = """
import bluesky.preprocessors as bpp
import bluesky.plan_stubs as bps


@bpp.set_run_key_decorator("run_2")
@bpp.run_decorator(md={})
def _multirun_plan_inner():
    npts, delay = 5, 1.0
    for j in range(npts):
        yield from bps.mov(motor1, j * 0.1 + 1, motor2, j * 0.2 - 2)
        yield from bps.trigger_and_read([motor1, motor2, det2])
        yield from bps.sleep(delay)


@bpp.set_run_key_decorator("run_1")
@bpp.run_decorator(md={})
def multirun_plan_nested():
    '''
    Multirun plan that is expected to produce 3 runs: 2 sequential runs nested in 1 outer run.
    '''
    npts, delay = 6, 1.0
    for j in range(int(npts / 2)):
        yield from bps.mov(motor, j * 0.2)
        yield from bps.trigger_and_read([motor, det])
        yield from bps.sleep(delay)

    yield from _multirun_plan_inner()

    yield from _multirun_plan_inner()

    for j in range(int(npts / 2), npts):
        yield from bps.mov(motor, j * 0.2)
        yield from bps.trigger_and_read([motor, det])
        yield from bps.sleep(delay)
"""


# fmt: off
@pytest.mark.parametrize("test_with_manager_restart", [False, True])
# fmt: on
def test_re_runs_1(re_manager_pc_copy, tmp_path, test_with_manager_restart):  # noqa: F811
    """
    Relatively complicated test for ``re_runs`` ZMQ API with multirun test. The same test
    is run with and without manager restart (API ``manager_kill``). Additionally
    the ``permissions_reload`` API was tested.
    """
    pc_path = copy_default_profile_collection(tmp_path)
    append_code_to_last_startup_file(pc_path, additional_code=_sample_multirun_plan1)

    # Generate the new list of allowed plans and devices and reload them
    gen_list_of_plans_and_devices(startup_dir=pc_path, file_dir=pc_path, overwrite=True)
    resp1, _ = zmq_single_request("permissions_reload")
    assert resp1["success"] is True, f"resp={resp1}"

    # Add plan to the queue
    params = {
        "item": {"name": "multirun_plan_nested", "item_type": "plan"},
        "user": _user,
        "user_group": _user_group,
    }
    resp2, _ = zmq_single_request("queue_item_add", params)
    assert resp2["success"] is True, f"resp={resp2}"

    # Open the environment
    resp3, _ = zmq_single_request("environment_open")
    assert resp3["success"] is True
    assert wait_for_condition(time=10, condition=condition_environment_created)

    # Get initial value of (empty) run list to capture changes in the run list
    resp, _ = zmq_single_request("status")
    run_list_uid = resp["run_list_uid"]

    # Start the queue
    resp4, _ = zmq_single_request("queue_start")
    assert resp4["success"] is True

    # The plan consists of 3 runs: runs #2 and #3 are sequential and enclosed in run #1.
    #   As the plan is executed we are going to look at the states of the executed runs.
    #   The sequence of possible states is known and we will capture all the states we can
    #   (by monitoring 'run_list_uid' in RE Monitor status). The states may occur only in
    #   the listed sequence, but some of the states are very unlikely to be hit, so they
    #   are marked as not required.
    run_list_states = [
        {"is_open": [True], "required": True},
        {"is_open": [True, True], "required": True},
        {"is_open": [True, False], "required": False},
        {"is_open": [True, False, True], "required": True},
        {"is_open": [True, False, False], "required": True},
        {"is_open": [False, False, False], "required": False},
        {"is_open": [], "required": False},
    ]
    # We will count the number of times the state was detected.
    states_found = [0] * len(run_list_states)

    # The index of the last state.
    n_last_state = -1

    # If test includes manager restart, then do the restart.
    if test_with_manager_restart:
        ttime.sleep(4)  # Let the plan work for a little bit.
        zmq_single_request("manager_kill")

    # Wait for the end of execution of the plan with timeout (60 seconds)
    time_finish = ttime.time() + 60
    while ttime.time() < time_finish:

        # If the manager was restarted, then wait for the manager to restart.
        #   All requests will time out until the manager is restarted.
        resp, _ = zmq_single_request("status")
        if test_with_manager_restart and not resp:
            # Wait until RE Manager is restarted
            continue
        else:
            # This is an error, raise the exception
            assert resp

        # Exit if the plan execution is completed
        if resp["manager_state"] == "idle":
            break

        # Check if 'run_list_uid' changed. If yes, then read and analyze the new 'run_list_uid'.
        if run_list_uid != resp["run_list_uid"]:
            run_list_uid = resp["run_list_uid"]
            # Use all supported combinations of options to load the 'run_list_uid'.
            resp_run_list1, _ = zmq_single_request("re_runs")
            resp_run_list2, _ = zmq_single_request("re_runs", params={"option": "active"})
            resp_run_list3, _ = zmq_single_request("re_runs", params={"option": "open"})
            resp_run_list4, _ = zmq_single_request("re_runs", params={"option": "closed"})
            full_list = resp_run_list1["run_list"]
            assert resp_run_list2["run_list"] == full_list
            assert resp_run_list3["run_list"] == [_ for _ in full_list if _["is_open"]]
            assert resp_run_list4["run_list"] == [_ for _ in full_list if not _["is_open"]]

            # Save full UID list (for all runs)
            if len(full_list) == 3:
                full_uid_list = [_["uid"] for _ in full_list]

            is_open_list = [_["is_open"] for _ in full_list]
            for n, state in enumerate(run_list_states):
                if state["is_open"] == is_open_list:
                    states_found[n] += 1
                    assert n > n_last_state, f"The Run List state #{n} was visited after state #{n_last_state}"
                    n_last_state = n
                    break

        ttime.sleep(0.1)

    # Since some states could be missed if RE Manager is restarted, we don't do the following check.
    if not test_with_manager_restart:
        for n_hits, state in zip(states_found, run_list_states):
            if state["required"]:
                assert n_hits == 1

    # Finally check the status (to ensure the plan was executed correctly).
    resp5a, _ = zmq_single_request("status")
    assert resp5a["items_in_queue"] == 0
    assert resp5a["items_in_history"] == 1
    # Also check if 'run_list_uid' was updated when the run list was cleared.
    if states_found[-1]:
        # The last state in the list is an empty run list. So UID is expected to remain the same.
        assert resp5a["run_list_uid"] == run_list_uid
    else:
        # UID is expected to change, because the run list is cleared at the end of plan execution.
        assert resp5a["run_list_uid"] != run_list_uid

    # Make sure that the run list is empty.
    resp5b, _ = zmq_single_request("re_runs")
    assert resp5b["success"] is True
    assert resp5b["msg"] == ""
    assert resp5b["run_list"] == []

    # Make sure that history contains correct data.
    resp5b, _ = zmq_single_request("history_get")
    assert resp5b["success"] is True
    history = resp5b["items"]
    assert len(history) == 1, str(resp5b)
    # Check that correct number of UIDs are saved in the history
    history_run_uids = history[0]["result"]["run_uids"]
    assert len(history_run_uids) == 3, str(resp5b)
    # Make sure that the list of UID in history matches the list of UIDs in the run list
    assert history_run_uids == full_uid_list

    # Close the environment
    resp6, _ = zmq_single_request("environment_close")
    assert resp6["success"] is True, f"resp={resp6}"
    assert wait_for_condition(time=5, condition=condition_environment_closed)


# =======================================================================================
#                 Tests for different scenarios of queue execution


def test_zmq_api_queue_execution_1(re_manager):  # noqa: F811
    """
    Execution of a queue that contains an instruction ('queue_stop').
    """

    # Instruction STOP
    params1a = {"item": _instruction_stop, "user": _user, "user_group": _user_group}
    resp1a, _ = zmq_single_request("queue_item_add", params1a)
    assert resp1a["success"] is True, f"resp={resp1a}"
    assert resp1a["msg"] == ""
    assert resp1a["item"]["name"] == "queue_stop"

    # Plan
    params1b = {"item": _plan1, "user": _user, "user_group": _user_group}
    resp1b, _ = zmq_single_request("queue_item_add", params1b)
    assert resp1b["success"] is True, f"resp={resp1b}"

    # Instruction STOP
    params1c = {"item": _instruction_stop, "user": _user, "user_group": _user_group}
    resp1c, _ = zmq_single_request("queue_item_add", params1c)
    assert resp1c["success"] is True, f"resp={resp1c}"
    assert resp1c["msg"] == ""
    assert resp1c["item"]["name"] == "queue_stop"

    # Plan
    params1d = {"item": _plan2, "user": _user, "user_group": _user_group}
    resp1d, _ = zmq_single_request("queue_item_add", params1d)
    assert resp1d["success"] is True, f"resp={resp1d}"

    # The queue contains only a single instruction (stop the queue).
    resp2, _ = zmq_single_request("environment_open")
    assert resp2["success"] is True
    assert wait_for_condition(time=10, condition=condition_environment_created)

    resp2a, _ = zmq_single_request("status")
    assert resp2a["items_in_queue"] == 4
    assert resp2a["items_in_history"] == 0

    resp3, _ = zmq_single_request("queue_start")
    assert resp3["success"] is True

    assert wait_for_condition(time=5, condition=condition_manager_idle)

    resp3a, _ = zmq_single_request("status")
    assert resp3a["items_in_queue"] == 3
    assert resp3a["items_in_history"] == 0

    resp4, _ = zmq_single_request("queue_start")
    assert resp4["success"] is True

    assert wait_for_condition(time=5, condition=condition_manager_idle)

    resp4a, _ = zmq_single_request("status")
    assert resp4a["items_in_queue"] == 1
    assert resp4a["items_in_history"] == 1

    resp5, _ = zmq_single_request("queue_start")
    assert resp5["success"] is True

    assert wait_for_condition(time=5, condition=condition_queue_processing_finished)

    resp5a, _ = zmq_single_request("status")
    assert resp5a["items_in_queue"] == 0
    assert resp5a["items_in_history"] == 2

    # Close the environment
    resp6, _ = zmq_single_request("environment_close")
    assert resp6["success"] is True, f"resp={resp6}"
    assert wait_for_condition(time=5, condition=condition_environment_closed)


class UidChecker:
    """
    The class may be used to verify if ``plan_queue_uid`` and ``plan_history_uid``
    changed by calling ``verify_uid_changes`` between operations.
    """

    def __init__(self):
        self.pq_uid, self.ph_uid = self.get_queue_uids()

    def get_queue_uids(self):
        status = get_queue_state()
        return status["plan_queue_uid"], status["plan_history_uid"]

    def verify_uid_changes(self, *, pq_changed, ph_changed):
        """
        Verify if ``plan_queue_uid`` and ``plan_history_uid`` changed
        since the last call to this function or instantiation of the class.

        Parameters
        ----------
        pq_changed : boolean
            indicates if ``plan_queue_uid`` is expected to change since last
            call to this function
        ph_changed : boolean
            indicates if ``plan_history_uid`` is expected to change since last
            call to this function
        """
        pq_uid_new, ph_uid_new = self.get_queue_uids()
        if pq_changed:
            assert pq_uid_new != self.pq_uid
        else:
            assert pq_uid_new == self.pq_uid

        if ph_changed:
            assert ph_uid_new != self.ph_uid
        else:
            assert ph_uid_new == self.ph_uid

        self.pq_uid, self.ph_uid = pq_uid_new, ph_uid_new


def test_zmq_api_queue_execution_2(re_manager):  # noqa: F811
    """
    Test if status fields ``plan_queue_uid`` and ``plan_history_uid`` are properly changed
    during execution of common queue operations.
    """
    uid_checker = UidChecker()

    # Plan
    params1b = {"item": _plan3, "user": _user, "user_group": _user_group}
    resp1b, _ = zmq_single_request("queue_item_add", params1b)
    assert resp1b["success"] is True, f"resp={resp1b}"
    uid_checker.verify_uid_changes(pq_changed=True, ph_changed=False)

    # Plan
    params1d = {"item": _plan3, "user": _user, "user_group": _user_group}
    resp1d, _ = zmq_single_request("queue_item_add", params1d)
    assert resp1d["success"] is True, f"resp={resp1d}"
    uid_checker.verify_uid_changes(pq_changed=True, ph_changed=False)

    # The queue contains only a single instruction (stop the queue).
    resp2, _ = zmq_single_request("environment_open")
    assert resp2["success"] is True
    assert wait_for_condition(time=10, condition=condition_environment_created)

    resp2a, _ = zmq_single_request("status")
    assert resp2a["items_in_queue"] == 2
    assert resp2a["items_in_history"] == 0

    uid_checker.verify_uid_changes(pq_changed=False, ph_changed=False)

    resp3, _ = zmq_single_request("queue_start")
    assert resp3["success"] is True
    ttime.sleep(1)
    uid_checker.verify_uid_changes(pq_changed=True, ph_changed=False)

    resp3a, _ = zmq_single_request("queue_stop")
    assert resp3a["success"] is True

    assert wait_for_condition(time=20, condition=condition_manager_idle)
    uid_checker.verify_uid_changes(pq_changed=True, ph_changed=True)

    resp3b, _ = zmq_single_request("status")
    assert resp3b["items_in_queue"] == 1
    assert resp3b["items_in_history"] == 1

    resp5, _ = zmq_single_request("queue_start")
    assert resp5["success"] is True
    ttime.sleep(1)
    uid_checker.verify_uid_changes(pq_changed=True, ph_changed=False)

    resp5a, _ = zmq_single_request("re_pause", params={"option": "immediate"})
    assert resp5a["success"] is True, str(resp5a)

    assert wait_for_condition(time=20, condition=condition_manager_paused)
    uid_checker.verify_uid_changes(pq_changed=False, ph_changed=False)

    resp5b, _ = zmq_single_request("re_stop")
    assert resp5b["success"] is True, str(resp5b)

    assert wait_for_condition(time=20, condition=condition_manager_idle)
    uid_checker.verify_uid_changes(pq_changed=True, ph_changed=True)

    resp5a, _ = zmq_single_request("status")
    assert resp5a["items_in_queue"] == 1
    assert resp5a["items_in_history"] == 2

    # Close the environment
    resp6, _ = zmq_single_request("environment_close")
    assert resp6["success"] is True, f"resp={resp6}"
    assert wait_for_condition(time=30, condition=condition_environment_closed)


# fmt: off
@pytest.mark.parametrize("test_mode", ["none", "ev"])
# fmt: on
def test_zmq_api_queue_execution_3(monkeypatch, re_manager_cmd, test_mode):  # noqa: F811
    """
    Test operation of RE Manager and 0MQ API with enabled encryption. Test options to
    set the server (RE Manager) private key using the environment variable.
    """
    public_key, private_key = generate_new_zmq_key_pair()

    if test_mode == "none":
        # No encryption
        pass
    elif test_mode == "ev":
        # Set server private key using environment variable
        monkeypatch.setenv("QSERVER_ZMQ_PRIVATE_KEY", private_key)
        set_qserver_zmq_public_key(monkeypatch, server_public_key=public_key)
    else:
        raise RuntimeError(f"Unrecognized test mode '{test_mode}'")

    re_manager_cmd([])

    # Plan
    params1b = {"item": _plan1, "user": _user, "user_group": _user_group}
    resp1b, _ = zmq_secure_request("queue_item_add", params1b)
    assert resp1b["success"] is True, f"resp={resp1b}"

    # Plan
    params1d = {"item": _plan2, "user": _user, "user_group": _user_group}
    resp1d, _ = zmq_secure_request("queue_item_add", params1d)
    assert resp1d["success"] is True, f"resp={resp1d}"

    params = {"user_group": _user_group}
    resp1, _ = zmq_secure_request("plans_allowed", params)
    resp2, _ = zmq_secure_request("devices_allowed", params)
    assert len(resp1["plans_allowed"])
    assert len(resp2["devices_allowed"])

    # The queue contains only a single instruction (stop the queue).
    resp2, _ = zmq_secure_request("environment_open")
    assert resp2["success"] is True
    assert wait_for_condition(time=10, condition=condition_environment_created)

    resp2a, _ = zmq_secure_request("status")
    assert resp2a["items_in_queue"] == 2
    assert resp2a["items_in_history"] == 0

    resp3, _ = zmq_secure_request("queue_start")
    assert resp3["success"] is True

    assert wait_for_condition(time=20, condition=condition_queue_processing_finished)

    resp5a, _ = zmq_secure_request("status")
    assert resp5a["items_in_queue"] == 0
    assert resp5a["items_in_history"] == 2

    # Close the environment
    resp6, _ = zmq_secure_request("environment_close")
    assert resp6["success"] is True, f"resp={resp6}"
    assert wait_for_condition(time=5, condition=condition_environment_closed)


"""
@pytest.mark.parametrize("a", [0] * 100)
def test_qserver_communication_reliability(re_manager, a):  # noqa: F811
    for i in range(10):
        print(f"i={i}")
        resp0, _ = zmq_single_request("status")
        assert resp0["manager_state"] == "idle"
        print(f"status: {resp0}")
"""

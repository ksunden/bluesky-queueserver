import asyncio
import pytest
import json
import copy
from bluesky_queueserver.manager.plan_queue_ops import PlanQueueOperations


errmsg_wrong_plan_type = "Parameter 'item' should be a dictionary"


@pytest.fixture
def pq():
    pq = PlanQueueOperations()
    asyncio.run(pq.start())
    # Clear any pool entries
    asyncio.run(pq.delete_pool_entries())
    yield pq
    # Don't leave any test entries in the pool
    asyncio.run(pq.delete_pool_entries())


def test_running_plan_info(pq):
    """
    Basic test for the following methods:
    `PlanQueueOperations.is_item_running()`
    `PlanQueueOperations.get_running_item_info()`
    `PlanQueueOperations.delete_pool_entries()`
    """

    async def testing():

        assert await pq.get_running_item_info() == {}
        assert await pq.is_item_running() is False

        some_plan = {"some_key": "some_value"}
        await pq._set_running_item_info(some_plan)
        assert await pq.get_running_item_info() == some_plan

        assert await pq.is_item_running() is True

        await pq._clear_running_item_info()
        assert await pq.get_running_item_info() == {}
        assert await pq.is_item_running() is False

        await pq._set_running_item_info(some_plan)
        await pq.delete_pool_entries()
        assert await pq.get_running_item_info() == {}
        assert await pq.is_item_running() is False

    asyncio.run(testing())


# fmt: off
@pytest.mark.parametrize("plan_running, plans, result_running, result_plans", [
    ({"testing": 1}, [{"testing": 2}, {"item_uid": "ab", "name": "nm"}, {"testing": 2}],
     {}, [{"item_uid": "ab", "name": "nm"}]),
    ({"testing": 1}, [{"testing": 2}, {"item_uid": "ab", "name": "nm"}, {"testing": 3}],
     {}, [{"item_uid": "ab", "name": "nm"}]),
    ({"item_uid": "a"}, [{"item_uid": "a1"}, {"item_uid": "a2"}, {"item_uid": "a3"}],
     {"item_uid": "a"}, [{"item_uid": "a1"}, {"item_uid": "a2"}, {"item_uid": "a3"}]),
])
# fmt: on
def test_queue_clean(pq, plan_running, plans, result_running, result_plans):
    """
    Test for ``_queue_clean()`` method
    """

    async def testing():
        await pq._set_running_item_info(plan_running)
        for plan in plans:
            await pq._r_pool.rpush(pq._name_plan_queue, json.dumps(plan))

        assert await pq.get_running_item_info() == plan_running
        plan_queue, _ = await pq.get_queue()
        assert plan_queue == plans

        await pq._queue_clean()

        assert await pq.get_running_item_info() == result_running
        plan_queue, _ = await pq.get_queue()
        assert plan_queue == result_plans

    asyncio.run(testing())


# fmt: off
@pytest.mark.parametrize("plan, result",
                         [({"a": 10}, True),
                          ([10, 20], False),
                          (50, False),
                          ("abc", False)])
# fmt: on
def test_verify_item_type(pq, plan, result):
    if result:
        pq._verify_item_type(plan)
    else:
        with pytest.raises(TypeError, match=errmsg_wrong_plan_type):
            pq._verify_item_type(plan)


# fmt: off
@pytest.mark.parametrize(
    "plan, f_kwargs, result, errmsg",
    [({"a": 10}, {}, False, "Item does not have UID"),
     ([10, 20], {}, False, errmsg_wrong_plan_type),
     ({"item_uid": "one"}, {}, True, ""),
     ({"item_uid": "two"}, {}, False, "Item with UID .+ is already in the queue"),
     ({"item_uid": "three"}, {}, False, "Item with UID .+ is already in the queue"),
     ({"item_uid": "two"}, {"ignore_uids": None}, False, "Item with UID .+ is already in the queue"),
     ({"item_uid": "two"}, {"ignore_uids": ["two"]}, True, ""),
     ({"item_uid": "two"}, {"ignore_uids": ["two", "three"]}, True, ""),
     ({"item_uid": "two"}, {"ignore_uids": ["one", "three"]}, False, "Item with UID .+ is already in the queue"),
     ])
# fmt: on
def test_verify_item(pq, plan, f_kwargs, result, errmsg):
    """
    Tests for method ``_verify_item()``.
    """
    # Set two existiing plans and then set one of them as running
    existing_plans = [{"item_uid": "two"}, {"item_uid": "three"}]

    async def set_plans():
        # Add plan to queue
        for plan in existing_plans:
            await pq.add_item_to_queue(plan)
        # Set one plan as currently running
        await pq.set_next_item_as_running()

        # Verify that setup is correct
        assert await pq.is_item_running() is True
        assert await pq.get_queue_size() == 1

    asyncio.run(set_plans())

    if result:
        pq._verify_item(plan, **f_kwargs)
    else:
        with pytest.raises(Exception, match=errmsg):
            pq._verify_item(plan, **f_kwargs)


def test_new_item_uid(pq):
    """
    Smoke test for the method ``new_item_uid()``.
    """
    assert isinstance(pq.new_item_uid(), str)


# fmt: off
@pytest.mark.parametrize("plan", [
    {"name": "a"},
    {"item_uid": "some_uid", "name": "a"},
])
# fmt: on
def test_set_new_item_uuid(pq, plan):
    """
    Basic test for the method ``set_new_item_uuid()``.
    """
    uid = plan.get("item_uid", None)

    # The function is supposed to create or replace UID
    new_plan = pq.set_new_item_uuid(plan)

    assert "item_uid" in new_plan
    assert isinstance(new_plan["item_uid"], str)
    assert new_plan["item_uid"] != uid


def test_get_index_by_uid(pq):
    """
    Test for ``_get_index_by_uid()``
    """
    plans = [
        {"item_uid": "a", "name": "name_a"},
        {"item_uid": "b", "name": "name_b"},
        {"item_uid": "c", "name": "name_c"},
    ]

    async def testing():
        for plan in plans:
            await pq.add_item_to_queue(plan)

        assert await pq._get_index_by_uid("b") == 1

        with pytest.raises(IndexError, match="No plan with UID 'nonexistent'"):
            assert await pq._get_index_by_uid("nonexistent")


def test_uid_dict_1(pq):
    """
    Basic test for functions associated with `_uid_dict`
    """
    plan_a = {"item_uid": "a", "name": "name_a"}
    plan_b = {"item_uid": "b", "name": "name_b"}
    plan_c = {"item_uid": "c", "name": "name_c"}

    plan_b_updated = {"item_uid": "b", "name": "name_b_updated"}

    pq._uid_dict_add(plan_a)
    pq._uid_dict_add(plan_b)

    assert pq._is_uid_in_dict(plan_a["item_uid"]) is True
    assert pq._is_uid_in_dict(plan_b["item_uid"]) is True
    assert pq._is_uid_in_dict(plan_c["item_uid"]) is False

    assert pq._uid_dict_get_item(plan_b["item_uid"]) == plan_b
    pq._uid_dict_update(plan_b_updated)
    assert pq._uid_dict_get_item(plan_b["item_uid"]) == plan_b_updated

    pq._uid_dict_remove(plan_a["item_uid"])
    assert pq._is_uid_in_dict(plan_a["item_uid"]) is False
    assert pq._is_uid_in_dict(plan_b["item_uid"]) is True

    pq._uid_dict_clear()
    assert pq._is_uid_in_dict(plan_a["item_uid"]) is False
    assert pq._is_uid_in_dict(plan_b["item_uid"]) is False


def test_uid_dict_2(pq):
    """
    Test if functions changing `pq._uid_dict` are also updating `pq.plan_queue_uid`.
    """
    plan_a = {"item_uid": "a", "name": "name_a"}
    plan_a_updated = {"item_uid": "a", "name": "name_a_updated"}

    pq_uid = pq.plan_queue_uid

    pq._uid_dict_add(plan_a)

    assert pq.plan_queue_uid != pq_uid
    pq_uid = pq.plan_queue_uid

    pq._uid_dict_update(plan_a_updated)

    assert pq.plan_queue_uid != pq_uid
    pq_uid = pq.plan_queue_uid

    pq._uid_dict_remove(plan_a_updated["item_uid"])

    assert pq.plan_queue_uid != pq_uid
    pq_uid = pq.plan_queue_uid

    pq._uid_dict_clear()

    assert pq.plan_queue_uid != pq_uid


def test_uid_dict_3_initialize(pq):
    """
    Basic test for functions associated with ``_uid_dict_initialize()``
    """

    async def testing():
        await pq.add_item_to_queue({"name": "a"})
        await pq.add_item_to_queue({"name": "b"})
        await pq.add_item_to_queue({"name": "c"})
        plans, _ = await pq.get_queue()
        uid_dict = {_["item_uid"]: _ for _ in plans}

        pq_uid = pq.plan_queue_uid

        await pq._uid_dict_initialize()

        assert pq._uid_dict == uid_dict
        assert pq.plan_queue_uid != pq_uid

    asyncio.run(testing())


def test_uid_dict_4_failing(pq):
    """
    Failing cases for functions associated with `_uid_dict`
    """
    plan_a = {"item_uid": "a", "name": "name_a"}
    plan_b = {"item_uid": "b", "name": "name_b"}
    plan_c = {"item_uid": "c", "name": "name_c"}

    pq._uid_dict_add(plan_a)
    pq._uid_dict_add(plan_b)

    # Add plan with UID that already exists
    pq_uid = pq.plan_queue_uid
    with pytest.raises(RuntimeError, match=f"'{plan_a['item_uid']}', which is already in the queue"):
        pq._uid_dict_add(plan_a)
    assert pq.plan_queue_uid == pq_uid

    assert len(pq._uid_dict) == 2

    # Remove plan with UID does not exist exists
    with pytest.raises(RuntimeError, match=f"'{plan_c['item_uid']}', which is not in the queue"):
        pq._uid_dict_remove(plan_c["item_uid"])
    assert pq.plan_queue_uid == pq_uid

    assert len(pq._uid_dict) == 2

    # Update plan with UID does not exist exists
    with pytest.raises(RuntimeError, match=f"'{plan_c['item_uid']}', which is not in the queue"):
        pq._uid_dict_update(plan_c)
    assert pq.plan_queue_uid == pq_uid

    assert len(pq._uid_dict) == 2


def test_remove_item(pq):
    """
    Basic test for functions associated with ``_remove_plan()``
    """

    async def testing():
        plan_list = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        for plan in plan_list:
            await pq.add_item_to_queue(plan)

        plans, _ = await pq.get_queue()
        plan_to_remove = [_ for _ in plans if _["name"] == "b"][0]

        # Remove one plan
        await pq._remove_item(plan_to_remove)
        plans, _ = await pq.get_queue()
        assert len(plans) == 2

        # Add a copy of a plan (queue is not supposed to have copies in real life)
        plan_to_add = plans[0]
        await pq._r_pool.lpush(pq._name_plan_queue, json.dumps(plan_to_add))
        # Now remove both plans
        await pq._remove_item(plan_to_add, single=False)  # Allow deleting multiple or no plans
        assert await pq.get_queue_size() == 1

        # Delete the plan again (the plan is not in the queue, but it shouldn't raise an exception)
        await pq._remove_item(plan_to_add, single=False)  # Allow deleting multiple or no plans
        assert await pq.get_queue_size() == 1

        with pytest.raises(RuntimeError, match="One item is expected"):
            await pq._remove_item(plan_to_add)
        assert await pq.get_queue_size() == 1

        # Now add 'plan_to_add' twice (create two copies)
        await pq._r_pool.lpush(pq._name_plan_queue, json.dumps(plan_to_add))
        await pq._r_pool.lpush(pq._name_plan_queue, json.dumps(plan_to_add))
        assert await pq.get_queue_size() == 3
        # Attempt to delete two copies
        with pytest.raises(RuntimeError, match="One item is expected"):
            await pq._remove_item(plan_to_add)
        # Exception is raised, but both copies are deleted
        assert await pq.get_queue_size() == 1

    asyncio.run(testing())


def test_get_queue_full_1(pq):
    """
    Basic test for the functions ``PlanQueueOperations.get_queue()`` and
    ``PlanQueueOperations.get_queue_full()``
    """

    async def testing():

        plans = [
            {"item_uid": "one", "name": "a"},
            {"item_uid": "two", "name": "b"},
            {"item_uid": "three", "name": "c"},
        ]

        for p in plans:
            await pq.add_item_to_queue(p)
        await pq.set_next_item_as_running()

        pq_uid = pq.plan_queue_uid
        queue1, uid1 = await pq.get_queue()
        running_item1 = await pq.get_running_item_info()
        queue2, running_item2, uid2 = await pq.get_queue_full()

        assert queue1 == plans[1:]
        assert queue2 == plans[1:]
        assert running_item1 == plans[0]
        assert running_item2 == plans[0]
        assert uid1 == pq_uid
        assert uid2 == pq_uid

    asyncio.run(testing())


# fmt: off
@pytest.mark.parametrize("params, name", [
    ({"pos": "front"}, "a"),
    ({"pos": "back"}, "c"),
    ({"pos": 0}, "a"),
    ({"pos": 1}, "b"),
    ({"pos": 2}, "c"),
    ({"pos": 3}, None),  # Index out of range
    ({"pos": -1}, "c"),
    ({"pos": -2}, "b"),
    ({"pos": -3}, "a"),
    ({"pos": -4}, None),  # Index out of range
    ({"uid": "one"}, "a"),
    ({"uid": "two"}, "b"),
    ({"uid": "nonexistent"}, None),
])
# fmt: on
def test_get_item_1(pq, params, name):
    """
    Basic test for the function ``PlanQueueOperations.get_item()``
    """

    async def testing():

        pq_uid = pq.plan_queue_uid
        await pq.add_item_to_queue({"item_uid": "one", "name": "a"})
        await pq.add_item_to_queue({"item_uid": "two", "name": "b"})
        await pq.add_item_to_queue({"item_uid": "three", "name": "c"})
        assert await pq.get_queue_size() == 3
        assert pq.plan_queue_uid != pq_uid

        if name is not None:
            plan = await pq.get_item(**params)
            assert plan["name"] == name
        else:
            msg = "Index .* is out of range" if "pos" in params else "is not in the queue"
            with pytest.raises(IndexError, match=msg):
                await pq.get_item(**params)

    asyncio.run(testing())


def test_get_item_2_fail(pq):
    """
    Basic test for the function ``PlanQueueOperations.get_item()``.
    Attempt to retrieve a running plan.
    """

    async def testing():

        await pq.add_item_to_queue({"item_uid": "one", "name": "a"})
        await pq.add_item_to_queue({"item_uid": "two", "name": "b"})
        await pq.add_item_to_queue({"item_uid": "three", "name": "c"})
        assert await pq.get_queue_size() == 3

        pq_uid = pq.plan_queue_uid
        await pq.set_next_item_as_running()
        assert await pq.get_queue_size() == 2
        assert pq.plan_queue_uid != pq_uid

        with pytest.raises(IndexError, match="is currently running"):
            await pq.get_item(uid="one")

        # Ambiguous parameters (position and UID is passed)
        with pytest.raises(ValueError, match="Ambiguous parameters"):
            await pq.get_item(pos=5, uid="abc")

    asyncio.run(testing())


def test_add_item_to_queue_1(pq):
    """
    Basic test for the function ``PlanQueueOperations.add_item_to_queue()``
    """

    async def add_plan(plan, n, **kwargs):
        plan_added, qsize = await pq.add_item_to_queue(plan, **kwargs)
        assert plan_added["name"] == plan["name"], f"plan: {plan}"
        assert qsize == n, f"plan: {plan}"

    async def testing():
        await add_plan({"name": "a"}, 1)
        await add_plan({"name": "b"}, 2)
        await add_plan({"name": "c"}, 3, pos="back")
        await add_plan({"name": "d"}, 4, pos="front")
        await add_plan({"name": "e"}, 5, pos=0)  # front
        await add_plan({"name": "f"}, 6, pos=5)  # back (index == queue size)
        await add_plan({"name": "g"}, 7, pos=5)  # previous to last
        await add_plan({"name": "h"}, 8, pos=-1)  # previous to last
        await add_plan({"name": "i"}, 9, pos=3)  # arbitrary index
        await add_plan({"name": "j"}, 10, pos=100)  # back (index some large number)
        await add_plan({"name": "k"}, 11, pos=-10)  # front (precisely negative queue size)
        await add_plan({"name": "l"}, 12, pos=-100)  # front (index some large negative number)

        assert await pq.get_queue_size() == 12

        plans, _ = await pq.get_queue()
        name_sequence = [_["name"] for _ in plans]
        assert name_sequence == ["l", "k", "e", "d", "a", "i", "b", "c", "g", "h", "f", "j"]

        await pq.clear_queue()

    asyncio.run(testing())


def test_add_item_to_queue_2(pq):
    """
    Basic test for the function ``PlanQueueOperations.add_item_to_queue()``
    """

    async def add_plan(plan, n, **kwargs):
        plan_added, qsize = await pq.add_item_to_queue(plan, **kwargs)
        assert plan_added["name"] == plan["name"], f"plan: {plan}"
        assert qsize == n, f"plan: {plan}"

    async def testing():
        await add_plan({"name": "a"}, 1)
        await add_plan({"name": "b"}, 2)
        await add_plan({"name": "c"}, 3, pos="back")

        plan_queue, _ = await pq.get_queue()
        displaced_uid = plan_queue[1]["item_uid"]

        await add_plan({"name": "d"}, 4, before_uid=displaced_uid)
        await add_plan({"name": "e"}, 5, after_uid=displaced_uid)

        # This reduces the number of elements in the queue by one
        await pq.set_next_item_as_running()

        displaced_uid = plan_queue[0]["item_uid"]
        await add_plan({"name": "f"}, 5, after_uid=displaced_uid)

        with pytest.raises(IndexError, match="Can not insert a plan in the queue before a currently running plan"):
            await add_plan({"name": "g"}, 5, before_uid=displaced_uid)

        with pytest.raises(IndexError, match="is not in the queue"):
            await add_plan({"name": "h"}, 5, before_uid="nonexistent_uid")

        assert await pq.get_queue_size() == 5

        plans, _ = await pq.get_queue()
        name_sequence = [_["name"] for _ in plans]
        assert name_sequence == ["f", "d", "b", "e", "c"]

        await pq.clear_queue()

    asyncio.run(testing())


def test_add_item_to_queue_3_fail(pq):
    """
    Failing tests for the function ``PlanQueueOperations.add_item_to_queue()``
    """

    async def testing():
        pq_uid = pq.plan_queue_uid
        with pytest.raises(ValueError, match="Parameter 'pos' has incorrect value"):
            await pq.add_item_to_queue({"name": "a"}, pos="something")
        assert pq.plan_queue_uid == pq_uid

        with pytest.raises(TypeError, match=errmsg_wrong_plan_type):
            await pq.add_item_to_queue("plan_is_not_string")
        assert pq.plan_queue_uid == pq_uid

        # Duplicate plan UID
        plan = {"item_uid": "abc", "name": "a"}
        await pq.add_item_to_queue(plan)
        pq_uid = pq.plan_queue_uid
        with pytest.raises(RuntimeError, match="Item with UID .+ is already in the queue"):
            await pq.add_item_to_queue(plan)
        assert pq.plan_queue_uid == pq_uid

        # Ambiguous parameters (position and UID is passed)
        with pytest.raises(ValueError, match="Ambiguous parameters"):
            await pq.add_item_to_queue({"name": "abc"}, pos=5, before_uid="abc")
        assert pq.plan_queue_uid == pq_uid

        # Ambiguous parameters ('before_uid' and 'after_uid' is specified)
        with pytest.raises(ValueError, match="Ambiguous parameters"):
            await pq.add_item_to_queue({"name": "abc"}, before_uid="abc", after_uid="abc")
        assert pq.plan_queue_uid == pq_uid

    asyncio.run(testing())


# fmt: off
@pytest.mark.parametrize("replace_uid", [False, True])
# fmt: on
def test_replace_item_1(pq, replace_uid):
    """
    Basic functionality of ``PlanQueueOperations.replace_item()`` function.
    """

    async def testing():
        plans = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        plans_added = [None] * len(plans)
        qsizes = [None] * len(plans)

        for n, plan in enumerate(plans):
            plans_added[n], qsizes[n] = await pq.add_item_to_queue(plan)

        assert qsizes == [1, 2, 3]
        assert await pq.get_queue_size() == 3

        # Change name, but keep UID
        plan_names_new = ["d", "e", "f"]
        for n in range(len(plans_added)):
            plan = plans_added[n].copy()
            plan["name"] = plan_names_new[n]

            uid_to_replace = plan["item_uid"]
            if replace_uid:
                plan["item_uid"] = pq.new_item_uid()  # Generate new UID

            pq_uid = pq.plan_queue_uid
            plan_new, qsize = await pq.replace_item(plan, item_uid=uid_to_replace)
            assert plan_new["name"] == plan["name"]
            assert plan_new["item_uid"] == plan["item_uid"]
            assert pq.plan_queue_uid != pq_uid

            assert await pq.get_queue_size() == 3
            plans_added[n] = plan_new

            # Make sure that the plan can be correctly extracted by uid
            assert await pq.get_item(uid=plan["item_uid"]) == plan
            assert await pq.get_queue_size() == 3

            # Initialize '_uid_dict' and see if the plan can still be extracted using correct UID.
            await pq._uid_dict_initialize()
            assert await pq.get_item(uid=plan["item_uid"]) == plan
            assert await pq.get_queue_size() == 3

    asyncio.run(testing())


def test_replace_item_2(pq):
    """
    ``PlanQueueOperations.replace_item()`` function: not UID in the plan - random UID is assigned.
    """

    async def testing():
        plans = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        plans_added = [None] * len(plans)
        qsizes = [None] * len(plans)

        for n, plan in enumerate(plans):
            plans_added[n], qsizes[n] = await pq.add_item_to_queue(plan)

        assert qsizes == [1, 2, 3]
        assert await pq.get_queue_size() == 3

        new_name = "h"
        plan_new = {"name": new_name}  # No UID in the plan. It should still be inserted
        pq_uid = pq.plan_queue_uid
        plan_replaced, qsize = await pq.replace_item(plan_new, item_uid=plans_added[1]["item_uid"])
        assert pq.plan_queue_uid != pq_uid

        new_uid = plan_replaced["item_uid"]
        assert new_uid != plans_added[1]["item_uid"]
        assert plan_replaced["name"] == plan_new["name"]

        plan = await pq.get_item(uid=new_uid)
        assert plan["item_uid"] == new_uid
        assert plan["name"] == new_name

        # Initialize '_uid_dict' and see if the plan can still be extracted using correct UID.
        await pq._uid_dict_initialize()
        plan = await pq.get_item(uid=new_uid)
        assert plan["item_uid"] == new_uid
        assert plan["name"] == new_name

    asyncio.run(testing())


def test_replace_item_3_failing(pq):
    """
    ``PlanQueueOperations.replace_item()`` - failing cases
    """

    async def testing():
        plans = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        plans_added = [None] * len(plans)
        qsizes = [None] * len(plans)

        for n, plan in enumerate(plans):
            plans_added[n], qsizes[n] = await pq.add_item_to_queue(plan)

        assert qsizes == [1, 2, 3]
        assert await pq.get_queue_size() == 3

        # Set the first item as 'running'
        running_plan = await pq.set_next_item_as_running()
        assert running_plan == plans[0]

        queue, _ = await pq.get_queue()
        running_item_info = await pq.get_running_item_info()

        pq_uid = pq.plan_queue_uid

        plan_new = {"name": "h"}  # No UID in the plan. It should still be inserted
        # Case: attempt to replace a plan that is currently running
        with pytest.raises(RuntimeError, match="Failed to replace item: Item with UID .* is currently running"):
            await pq.replace_item(plan_new, item_uid=running_plan["item_uid"])
        assert pq.plan_queue_uid == pq_uid

        # Case: attempt to replace a plan that is not in queue
        with pytest.raises(RuntimeError, match="Failed to replace item: Item with UID .* is not in the queue"):
            await pq.replace_item(plan_new, item_uid="uid-that-does-not-exist")
        assert pq.plan_queue_uid == pq_uid

        # Case: attempt to replace a plan with another plan that already exists in the queue
        plan = plans_added[1]
        with pytest.raises(RuntimeError, match="Item with UID .* is already in the queue"):
            await pq.replace_item(plan, item_uid=plans_added[2]["item_uid"])
        assert pq.plan_queue_uid == pq_uid

        # Case: attempt to replace a plan with currently running plan
        with pytest.raises(RuntimeError, match="Item with UID .* is already in the queue"):
            await pq.replace_item(running_plan, item_uid=plans_added[2]["item_uid"])
        assert pq.plan_queue_uid == pq_uid

        # Make sure that the queue did not change during the test
        plan_queue, _ = await pq.get_queue()
        assert plan_queue == queue
        assert await pq.get_running_item_info() == running_item_info

    asyncio.run(testing())


# fmt: off
@pytest.mark.parametrize("params, src, order, success, pquid_changed, msg", [
    ({"pos": 1, "pos_dest": 1}, 1, "abcde", True, False, ""),
    ({"pos": "front", "pos_dest": "front"}, 0, "abcde", True, False, ""),
    ({"pos": "back", "pos_dest": "back"}, 4, "abcde", True, False, ""),
    ({"pos": "front", "pos_dest": "back"}, 0, "bcdea", True, True, ""),
    ({"pos": "back", "pos_dest": "front"}, 4, "eabcd", True, True, ""),
    ({"pos": 1, "pos_dest": 2}, 1, "acbde", True, True, ""),
    ({"pos": 2, "pos_dest": 1}, 2, "acbde", True, True, ""),
    ({"pos": 0, "pos_dest": 4}, 0, "bcdea", True, True, ""),
    ({"pos": 4, "pos_dest": 0}, 4, "eabcd", True, True, ""),
    ({"pos": 3, "pos_dest": "front"}, 3, "dabce", True, True, ""),
    ({"pos": 2, "pos_dest": "back"}, 2, "abdec", True, True, ""),
    ({"uid": "p3", "after_uid": "p3"}, 2, "abcde", True, False, ""),
    ({"uid": "p1", "before_uid": "p2"}, 0, "abcde", True, True, ""),
    ({"uid": "p1", "after_uid": "p2"}, 0, "bacde", True, True, ""),
    ({"uid": "p2", "pos_dest": "front"}, 1, "bacde", True, True, ""),
    ({"uid": "p2", "pos_dest": "back"}, 1, "acdeb", True, True, ""),
    ({"uid": "p1", "pos_dest": "front"}, 0, "abcde", True, False, ""),
    ({"uid": "p5", "pos_dest": "back"}, 4, "abcde", True, False, ""),
    ({"pos": 1, "after_uid": "p4"}, 1, "acdbe", True, True, ""),
    ({"pos": "front", "after_uid": "p4"}, 0, "bcdae", True, True, ""),
    ({"pos": 3, "after_uid": "p1"}, 3, "adbce", True, True, ""),
    ({"pos": "back", "after_uid": "p1"}, 4, "aebcd", True, True, ""),
    ({"pos": 1, "before_uid": "p4"}, 1, "acbde", True, True, ""),
    ({"pos": "front", "before_uid": "p4"}, 0, "bcade", True, True, ""),
    ({"pos": 3, "before_uid": "p1"}, 3, "dabce", True, True, ""),
    ({"pos": "back", "before_uid": "p1"}, 4, "eabcd", True, True, ""),
    ({"pos": "back", "after_uid": "p5"}, 4, "abcde", True, False, ""),
    ({"pos": "front", "before_uid": "p1"}, 0, "abcde", True, False, ""),
    ({"pos": 50, "before_uid": "p1"}, 0, "", False, False, r"Source plan \(position 50\) was not found"),
    ({"uid": "abc", "before_uid": "p1"}, 0, "", False, False, r"Source plan \(UID 'abc'\) was not found"),
    ({"pos": 3, "pos_dest": 50}, 0, "", False, False, r"Destination plan \(position 50\) was not found"),
    ({"uid": "p1", "before_uid": "abc"}, 0, "", False, False, r"Destination plan \(UID 'abc'\) was not found"),
    ({"before_uid": "p1"}, 0, "", False, None, r"Source position or UID is not specified"),
    ({"pos": 3}, 0, "", False, False, r"Destination position or UID is not specified"),
    ({"pos": 1, "uid": "p1", "before_uid": "p4"}, 1, "", False, False, "Ambiguous parameters"),
    ({"pos": 1, "pos_dest": 4, "before_uid": "p4"}, 1, "", False, False, "Ambiguous parameters"),
    ({"pos": 1, "after_uid": "p4", "before_uid": "p4"}, 1, "", False, False, "Ambiguous parameters"),
])
# fmt: on
def test_move_item_1(pq, params, src, order, success, pquid_changed, msg):
    """
    Basic tests for ``move_item()``.
    """

    async def testing():
        plans = [
            {"item_uid": "p1", "name": "a"},
            {"item_uid": "p2", "name": "b"},
            {"item_uid": "p3", "name": "c"},
            {"item_uid": "p4", "name": "d"},
            {"item_uid": "p5", "name": "e"},
        ]

        for plan in plans:
            await pq.add_item_to_queue(plan)

        assert await pq.get_queue_size() == len(plans)
        pq_uid = pq.plan_queue_uid

        if success:
            plan, qsize = await pq.move_item(**params)
            assert qsize == len(plans)
            assert plan["name"] == plans[src]["name"]

            queue, _ = await pq.get_queue()
            names = [_["name"] for _ in queue]
            names = "".join(names)
            assert names == order

            if pquid_changed:
                assert pq.plan_queue_uid != pq_uid
            else:
                assert pq.plan_queue_uid == pq_uid

        else:
            with pytest.raises(Exception, match=msg):
                await pq.move_item(**params)
            assert pq.plan_queue_uid == pq_uid

    asyncio.run(testing())


# fmt: off
@pytest.mark.parametrize("pos, name", [
    ("front", "a"),
    ("back", "c"),
    (0, "a"),
    (1, "b"),
    (2, "c"),
    (3, None),  # Index out of range
    (-1, "c"),
    (-2, "b"),
    (-3, "a"),
    (-4, None)  # Index out of range
])
# fmt: on
def test_pop_item_from_queue_1(pq, pos, name):
    """
    Basic test for the function ``PlanQueueOperations.pop_item_from_queue()``
    """

    async def testing():

        await pq.add_item_to_queue({"name": "a"})
        await pq.add_item_to_queue({"name": "b"})
        await pq.add_item_to_queue({"name": "c"})
        assert await pq.get_queue_size() == 3

        pq_uid = pq.plan_queue_uid

        if name is not None:
            plan, qsize = await pq.pop_item_from_queue(pos=pos)
            assert plan["name"] == name
            assert qsize == 2
            assert await pq.get_queue_size() == 2
            # Push the plan back to the queue (proves that UID is removed from '_uid_dict')
            await pq.add_item_to_queue(plan)
            assert await pq.get_queue_size() == 3
            assert pq.plan_queue_uid != pq_uid
        else:
            with pytest.raises(IndexError, match="Index .* is out of range"):
                await pq.pop_item_from_queue(pos=pos)
            assert pq.plan_queue_uid == pq_uid

    asyncio.run(testing())


@pytest.mark.parametrize("pos", ["front", "back", 0, 1, -1])
def test_pop_item_from_queue_2(pq, pos):
    """
    Test for the function ``PlanQueueOperations.pop_item_from_queue()``:
    the case of empty queue.
    """

    async def testing():
        assert await pq.get_queue_size() == 0
        pq_uid = pq.plan_queue_uid
        with pytest.raises(IndexError, match="Index .* is out of range|Queue is empty"):
            await pq.pop_item_from_queue(pos=pos)
        assert pq.plan_queue_uid == pq_uid

    asyncio.run(testing())


def test_pop_item_from_queue_3(pq):
    """
    Pop plans by UID.
    """

    async def testing():
        await pq.add_item_to_queue({"name": "a"})
        await pq.add_item_to_queue({"name": "b"})
        await pq.add_item_to_queue({"name": "c"})
        assert await pq.get_queue_size() == 3

        plans, _ = await pq.get_queue()
        assert len(plans) == 3
        plan_to_remove = [_ for _ in plans if _["name"] == "b"][0]

        # Remove one plan
        pq_uid = pq.plan_queue_uid
        await pq.pop_item_from_queue(uid=plan_to_remove["item_uid"])
        assert await pq.get_queue_size() == 2
        assert pq.plan_queue_uid != pq_uid

        # Attempt to remove the plan again. This should raise an exception.
        pq_uid = pq.plan_queue_uid
        with pytest.raises(
            IndexError, match=f"Plan with UID '{plan_to_remove['item_uid']}' " f"is not in the queue"
        ):
            await pq.pop_item_from_queue(uid=plan_to_remove["item_uid"])
        assert await pq.get_queue_size() == 2
        assert pq.plan_queue_uid == pq_uid

        # Attempt to remove the plan that is running. This should raise an exception.
        await pq.set_next_item_as_running()
        assert await pq.get_queue_size() == 1
        pq_uid = pq.plan_queue_uid
        with pytest.raises(IndexError, match="Can not remove an item which is currently running"):
            await pq.pop_item_from_queue(uid=plans[0]["item_uid"])
        assert await pq.get_queue_size() == 1
        assert pq.plan_queue_uid == pq_uid

    asyncio.run(testing())


def test_pop_item_from_queue_4_fail(pq):
    """
    Failing tests for the function ``PlanQueueOperations.pop_item_from_queue()``
    """

    async def testing():
        pq_uid = pq.plan_queue_uid
        with pytest.raises(ValueError, match="Parameter 'pos' has incorrect value"):
            await pq.pop_item_from_queue(pos="something")
        assert pq.plan_queue_uid == pq_uid

        # Ambiguous parameters (position and UID is passed)
        with pytest.raises(ValueError, match="Ambiguous parameters"):
            await pq.pop_item_from_queue(pos=5, uid="abc")
        assert pq.plan_queue_uid == pq_uid

    asyncio.run(testing())


def test_clear_queue(pq):
    """
    Test for ``PlanQueueOperations.clear_queue`` function
    """

    async def testing():
        await pq.add_item_to_queue({"name": "a"})
        await pq.add_item_to_queue({"name": "b"})
        await pq.add_item_to_queue({"name": "c"})
        # Set one of 3 plans as running (removes it from the queue)
        await pq.set_next_item_as_running()

        assert await pq.get_queue_size() == 2
        assert len(pq._uid_dict) == 3

        pq_uid = pq.plan_queue_uid

        # Clears the queue only (doesn't touch the running plan)
        await pq.clear_queue()

        assert await pq.get_queue_size() == 0
        assert len(pq._uid_dict) == 1
        assert pq.plan_queue_uid != pq_uid

        with pytest.raises(ValueError, match="Parameter 'pos' has incorrect value"):
            await pq.pop_item_from_queue(pos="something")

    asyncio.run(testing())


def test_add_to_history_functions(pq):
    """
    Test for ``PlanQueueOperations._add_to_history()`` method.
    """

    async def testing():
        assert await pq.get_history_size() == 0

        plans = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        ph_uid = pq.plan_history_uid
        for plan in plans:
            await pq._add_to_history(plan)
        assert await pq.get_history_size() == 3
        assert pq.plan_history_uid != ph_uid

        ph_uid = pq.plan_history_uid
        plan_history, plan_history_uid_1 = await pq.get_history()
        assert pq.plan_history_uid == plan_history_uid_1
        assert pq.plan_history_uid == ph_uid

        assert len(plan_history) == 3
        assert plan_history == plans

        ph_uid = pq.plan_history_uid
        await pq.clear_history()
        assert pq.plan_history_uid != ph_uid

        plan_history, _ = await pq.get_history()
        assert plan_history == []

    asyncio.run(testing())


def test_set_next_item_as_running(pq):
    """
    Test for ``PlanQueueOperations.set_next_item_as_running()`` function
    """

    async def testing():
        # Apply to empty queue
        assert await pq.get_queue_size() == 0
        assert await pq.is_item_running() is False
        assert await pq.set_next_item_as_running() == {}
        assert await pq.get_queue_size() == 0
        assert await pq.is_item_running() is False

        # Apply to a queue with several plans
        await pq.add_item_to_queue({"name": "a"})
        await pq.add_item_to_queue({"name": "b"})
        await pq.add_item_to_queue({"name": "c"})

        # Set one of 3 plans as running (removes it from the queue)
        pq_uid = pq.plan_queue_uid
        assert await pq.set_next_item_as_running() != {}

        assert pq.plan_queue_uid != pq_uid
        assert await pq.get_queue_size() == 2
        assert len(pq._uid_dict) == 3

        # Apply if a plan is already running
        pq_uid = pq.plan_queue_uid
        assert await pq.set_next_item_as_running() == {}
        assert pq.plan_queue_uid == pq_uid
        assert await pq.get_queue_size() == 2
        assert len(pq._uid_dict) == 3

    asyncio.run(testing())


def test_set_processed_item_as_completed(pq):
    """
    Test for ``PlanQueueOperations.set_processed_item_as_completed()`` function.
    The function moves currently running plan to history.
    """

    plans = [{"item_uid": 1, "name": "a"}, {"item_uid": 2, "name": "b"}, {"item_uid": 3, "name": "c"}]
    plans_run_uids = [["abc1"], ["abc2", "abc3"], []]

    def add_status_to_plans(plans, run_uids, exit_status):
        plans = copy.deepcopy(plans)
        plans_modified = []
        for plan, run_uid in zip(plans, run_uids):
            plan.setdefault("result", {})
            plan["result"]["exit_status"] = exit_status
            plan["result"]["run_uids"] = run_uid
            plans_modified.append(plan)
        return plans_modified

    async def testing():
        for plan in plans:
            await pq.add_item_to_queue(plan)

        # No plan is running
        pq_uid = pq.plan_queue_uid
        ph_uid = pq.plan_history_uid
        plan = await pq.set_processed_item_as_completed(exit_status="completed", run_uids=plans_run_uids[0])
        assert plan == {}
        assert pq.plan_queue_uid == pq_uid
        assert pq.plan_history_uid == ph_uid

        # Execute the first plan
        await pq.set_next_item_as_running()
        pq_uid = pq.plan_queue_uid
        plan = await pq.set_processed_item_as_completed(exit_status="completed", run_uids=plans_run_uids[0])
        assert pq.plan_queue_uid != pq_uid
        assert pq.plan_history_uid != ph_uid

        assert await pq.get_queue_size() == 2
        assert await pq.get_history_size() == 1
        assert plan["name"] == plans[0]["name"]
        assert plan["result"]["exit_status"] == "completed"
        assert plan["result"]["run_uids"] == plans_run_uids[0]

        plan_history, _ = await pq.get_history()
        plan_history_expected = add_status_to_plans(plans[0:1], plans_run_uids[0:1], "completed")
        assert plan_history == plan_history_expected

        # Execute the second plan
        await pq.set_next_item_as_running()
        plan = await pq.set_processed_item_as_completed(exit_status="completed", run_uids=plans_run_uids[1])

        assert await pq.get_queue_size() == 1
        assert await pq.get_history_size() == 2
        assert plan["name"] == plans[1]["name"]
        assert plan["result"]["exit_status"] == "completed"
        assert plan["result"]["run_uids"] == plans_run_uids[1]

        plan_history, _ = await pq.get_history()
        plan_history_expected = add_status_to_plans(plans[0:2], plans_run_uids[0:2], "completed")
        assert plan_history == plan_history_expected

    asyncio.run(testing())


def test_set_processed_item_as_stopped(pq):
    """
    Test for ``PlanQueueOperations.set_processed_item_as_stopped()`` function.
    The function pushes running plan back to the queue and saves it in history as well.

    Typically execution of single-run plans result in no UIDs, but in this test we still assign UIDS
    to test functionality.
    """
    plans = [{"item_uid": 1, "name": "a"}, {"item_uid": 2, "name": "b"}, {"item_uid": 3, "name": "c"}]
    plans_run_uids = [["abc1"], ["abc2", "abc3"], []]

    def add_status_to_plans(plans, run_uids, exit_status):
        plans = copy.deepcopy(plans)
        plans_modified = []
        for plan, run_uid in zip(plans, run_uids):
            plan.setdefault("result", {})
            plan["result"]["exit_status"] = exit_status
            plan["result"]["run_uids"] = run_uid
            plans_modified.append(plan)
        return plans_modified

    async def testing():
        for plan in plans:
            await pq.add_item_to_queue(plan)

        # No plan is running
        pq_uid = pq.plan_queue_uid
        ph_uid = pq.plan_history_uid
        plan = await pq.set_processed_item_as_stopped(exit_status="stopped", run_uids=plans_run_uids[0])
        assert plan == {}
        assert pq.plan_queue_uid == pq_uid
        assert pq.plan_history_uid == ph_uid

        # Execute the first plan
        await pq.set_next_item_as_running()
        pq_uid = pq.plan_queue_uid
        plan = await pq.set_processed_item_as_stopped(exit_status="stopped", run_uids=plans_run_uids[0])
        assert pq.plan_queue_uid != pq_uid
        assert pq.plan_history_uid != ph_uid

        assert await pq.get_queue_size() == 3
        assert await pq.get_history_size() == 1
        assert plan["name"] == plans[0]["name"]
        assert plan["result"]["exit_status"] == "stopped"
        assert plan["result"]["run_uids"] == plans_run_uids[0]

        plan_history, _ = await pq.get_history()
        plan_history_expected = add_status_to_plans([plans[0]], [plans_run_uids[0]], "stopped")
        assert plan_history == plan_history_expected

        # Execute the second plan
        await pq.set_next_item_as_running()
        plan = await pq.set_processed_item_as_stopped(exit_status="stopped", run_uids=plans_run_uids[1])

        assert await pq.get_queue_size() == 3
        assert await pq.get_history_size() == 2
        assert plan["name"] == plans[0]["name"]
        assert plan["result"]["exit_status"] == "stopped"
        assert plan["result"]["run_uids"] == plans_run_uids[1]

        plan_history, _ = await pq.get_history()
        plan_history_expected = add_status_to_plans(
            [plans[0].copy(), plans[0].copy()], [plans_run_uids[0], plans_run_uids[1]], "stopped"
        )
        assert plan_history == plan_history_expected

    asyncio.run(testing())

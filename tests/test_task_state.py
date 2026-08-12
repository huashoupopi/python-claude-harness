"""A 组:Task 生命周期状态机。

钉住 create → can_start → claim → complete 这条链上的现有行为。
T19 要把 task 系统搬进主干,这些断言是搬运过程中的报警器:
红了不一定是错,但一定是「行为变了」,必须是有意为之。
"""

import pytest


def test_sandbox_is_really_isolated(sandbox, tmp_path):
    """开工前的定心丸:任务目录落在 pytest 临时区,不在真仓库,且开局为空。"""
    assert sandbox.TASKS_DIR == tmp_path / ".tasks"
    assert sandbox.TASKS_DIR.is_dir()
    assert sandbox.list_tasks() == []


def test_create_task(sandbox):
    """新建的任务一律是 pending;两次创建拿到不同 id。

    ⚠️ 第二条断言有已知隐患:id = task_<秒级时间戳>_<四位随机数>,
    同一秒内创建有 1/10000 概率撞号。概率太小测不出来,但风险是真的。
    与 test_list_tasks_... 里的顺序不稳定同根:秒级时间戳分辨率不够。
    """
    task = sandbox.create_task("echo hello", "打印hello")
    assert task.status == sandbox.TaskStatus.PENDING
    task2 = sandbox.create_task("echo world", "打印world")
    assert task.id != task2.id


def test_can_start(sandbox):
    """依赖门:blockedBy 里的任务全部 completed 之前,后续任务不能开工。

    注意 can_start 只认 COMPLETED——依赖处于 in_progress 也照样拦。
    """
    task1 = sandbox.create_task("echo hello", "打印hello")
    task2 = sandbox.create_task("echo world", "打印world", blockedBy=[task1.id])
    assert sandbox.can_start(task2.id) is False
    sandbox.claim_task(task1.id)
    sandbox.complete_task(task1.id)
    assert sandbox.can_start(task2.id) is True


def test_claim_task(sandbox):
    """重复认领被拒:第二个人拿到错误信息,不会静默抢走任务。

    走的是 status 那道闸(任务已是 in_progress),不是 owner 那道——
    因为 claim 成功时 owner 和 status 是一起变的,status 检查永远先命中。
    """
    task = sandbox.create_task("echo hello", "打印hello")
    sandbox.claim_task(task.id, owner="alice")
    result = sandbox.claim_task(task.id, owner="bob")
    assert "cannot claim" in result


def test_claim_cannot_start_task(sandbox):
    """依赖没完成时认领被拒,且错误信息里说明卡在哪个依赖上。"""
    task1 = sandbox.create_task("echo hello", "打印hello")
    task2 = sandbox.create_task("echo world", "打印world", blockedBy=[task1.id])
    result = sandbox.claim_task(task2.id, owner="alice")
    assert "Cannot start" in result


def test_unclaim_complete_task(sandbox):
    """不能跳过认领直接完成:complete 只接受 in_progress 的任务。

    这条挡住的是「任务没人做过却被标记完成」——依赖门靠 COMPLETED 判断,
    一旦能凭空完成,整条依赖链就形同虚设。
    """
    task = sandbox.create_task("echo hello", "打印hello")
    result = sandbox.complete_task(task.id)
    assert "cannot complete" in result


def test_load_missing_task_raises(sandbox):
    """load_task 对不存在的 id 直接抛 FileNotFoundError,不返回 None、不给默认值。

    调用方必须自己防御。can_start 就是这么做的——它先 _task_path(dep).exists()
    再 load,否则一个被删掉的依赖会让整条链炸掉。
    """
    with pytest.raises(FileNotFoundError):
        sandbox.load_task("nonexistent-id")


def test_list_tasks_returns_all_but_order_is_not_creation_order(sandbox):
    """list_tasks 返回全部任务,但顺序是文件名字典序,不是创建顺序。

    id 的时间戳只到秒,同秒创建时排序退化成比随机数 → 顺序不稳定。
    所以这里只断言集合,不断言顺序。T19 若有代码依赖顺序,会随机出错。
    """
    task1 = sandbox.create_task("echo hello", "打印hello")
    task2 = sandbox.create_task("echo world", "打印world")
    tasks = sandbox.list_tasks()
    assert {t.id for t in tasks} == {task1.id, task2.id}

"""G 组:沙箱隔离。验收标准 = 用【真实发生过的两次越界】当测试用例。

这两条不是假想的攻击,是这个项目里实际发生过的:

  第四个故事(2026-08-12, T19 自测)
      给文件工具加了 safe_path 工作区防护,write_file 越界被 permission_hook 正确拦下,
      但模型随即改用 `bash echo x > 越界路径` 写成功了,还在回复里明说
      "I'll use bash instead"。
      → bash 是万能工具,进程内的细粒度权限检查对它无效。

  第五个故事(2026-08-15, T22 正式跑批)
      模型做不出 t08(连写 48 次 write_file 仍不过),掉头去查评测系统,
      cat 了 bench/tasks/t08_wide_validators/solution/*.py(标准答案),
      并在回复里写着「已对齐 solution,测试全绿」。泄漏 2/120。
      → 专门写的防作弊闸 tests_tampered 一点反应都没有:
        它防的是【改测试】,模型走的是【抄答案】。

🪝 一写一读,正好是文件系统的两个方向;而两次防护失效的形状是同一个:
   **黑名单和白名单都是「列举」,而攻击面不可穷举;只有隔离是「构造性」的。**

📌 为什么用真实案例当验收:抽象的「越界命令被挡下」没法判断做没做到 —— 这两条能。
"""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

DOCKER = shutil.which("docker")
TRUNK = Path(__file__).parent.parent / "examples" / "22_trunk.py"
REAL_SOLUTION = (
    Path(__file__).parent.parent
    / "bench/tasks/t08_wide_validators/solution/check_email.py"
)


def _daemon_up() -> bool:
    if not DOCKER:
        return False
    try:
        return subprocess.run([DOCKER, "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _sandbox_image() -> str:
    """镜像名的【唯一真相】在主干 SANDBOX_IMAGE,这里不复制那个默认值。

    为一个常量装载整个主干确实重(几十毫秒),但代价是可预期的;
    复制一份 "harness-sandbox:1" 的代价是【改名字那天守卫悄悄失效】,
    而那种代价发现不了。仓里已经欠着「两份黑名单」的债,不再添第三份。
    """
    spec = importlib.util.spec_from_file_location("_guard_trunk", TRUNK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.SANDBOX_IMAGE


def _image_ready() -> bool:
    """⚠️ daemon 活着 ≠ 沙箱能跑 —— 还得镜像真的 build 过。

    2026-08-16 CI 实测:runner 上 docker 装了、daemon 也起着,于是旧守卫放行,
    结果 `docker run` 撞上【不存在的镜像】,exit 125,两条测试红在 CI 上。
    本机一直绿,只是因为本机早就 build 过。
    🪝 同族(今天第二次):代码依赖了「当前环境恰好有某个东西」。
    """
    if not _daemon_up():
        return False
    try:
        return subprocess.run(
            [DOCKER, "image", "inspect", _sandbox_image()], capture_output=True, timeout=10
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


needs_docker = pytest.mark.skipif(
    not _image_ready(),
    reason="需要 docker daemon + 已 build 的沙箱镜像(docker build -t harness-sandbox:1 sandbox/)",
)


@pytest.fixture
def sbx(trunk, tmp_path, monkeypatch):
    """把工作区指到 tmp_path 并开启沙箱。用完销毁容器。

    ⚠️ 必须 monkeypatch WORKDIR:否则挂进盒子的是【真实项目根】,
       那 solution/ 就在挂载点里面了 —— 测试会假绿。
    """
    monkeypatch.setattr(trunk, "WORKDIR", tmp_path)
    monkeypatch.setattr(trunk, "SANDBOX_MODE", "docker")
    monkeypatch.setattr(trunk, "_sandbox_name", None)
    yield trunk
    trunk.stop_sandbox()


@needs_docker
def test_sandbox_blocks_write_outside_workspace(sbx, tmp_path):
    """🔴 第四个故事:bash 写工作区【外】的文件,必须失败。

    这是 write_file 那道 safe_path 检查【挡不住】的那条路 ——
    模型当时的原话是 "I'll use bash instead"。
    """
    outside = tmp_path.parent / "escaped_by_bash.txt"
    if outside.exists():
        outside.unlink()
    sbx.run_bash(f"echo pwned > {outside}")
    assert not outside.exists(), f"越界写成功了!{outside} 被创建出来 —— 沙箱没挡住"


@needs_docker
def test_sandbox_blocks_read_outside_workspace(sbx):
    """🔴 第五个故事:bash 读工作区【外】的文件,必须失败。

    直接拿【今天真被读到的那个文件】当靶子。
    ⚠️ 这条测的是【读】—— 现有防护(safe_path / DENY_LIST / tests_tampered)
       没有一条管读。
    """
    assert REAL_SOLUTION.exists(), "靶子文件不在了,测试本身失效"
    leaked = REAL_SOLUTION.read_text()
    out = sbx.run_bash(f"cat {REAL_SOLUTION}")
    assert "def is_email" not in out, f"标准答案被读出来了:{out[:200]}"
    assert leaked.strip() not in out


@needs_docker
def test_sandbox_allows_normal_work(sbx, tmp_path):
    """✅ 反面同样要验:正常做题不受影响。

    隔离做过头就成了「什么都干不了」—— 那不叫安全,叫坏了。
    🪝 双头验的老规矩:光证明「坏事被挡住」不够,还得证明「好事没被挡住」。
    """
    (tmp_path / "hello.py").write_text("print('sandbox works')\n")
    assert "sandbox works" in sbx.run_bash("python hello.py")

    # 写:盒子里写的文件,宿主机上看得见(挂载是双向的)
    sbx.run_bash("echo inside > made_in_box.txt")
    assert (tmp_path / "made_in_box.txt").exists()

    # 考场的命脉:pytest 必须能跑(镜像里预装了 —— 断网之后装不了)
    (tmp_path / "test_x.py").write_text("def test_ok():\n    assert 1 == 1\n")
    assert "1 passed" in sbx.run_bash("python -m pytest -q")


NET_PROBE = "python -c \"import socket; socket.create_connection(('1.1.1.1',53),timeout=3)\""


@needs_docker
def test_network_can_be_cut_explicitly(trunk, tmp_path, monkeypatch):
    """显式要求断网时,网必须是断的 —— bench 跑批用这一档。

    无人看守的批量跑最怕数据被发出去,所以 run_bench 会显式设 SANDBOX_NETWORK=none。
    """
    monkeypatch.setattr(trunk, "WORKDIR", tmp_path)
    monkeypatch.setattr(trunk, "SANDBOX_MODE", "docker")
    monkeypatch.setattr(trunk, "SANDBOX_NETWORK", "none")
    monkeypatch.setattr(trunk, "_sandbox_name", None)
    try:
        out = trunk.run_bash(NET_PROBE)
        assert "Traceback" in out or "rror" in out, f"要求断网却通了:{out[:200]}"
    finally:
        trunk.stop_sandbox()


@needs_docker
def test_sandbox_does_not_silently_cut_network(sbx):
    """🔴 打开沙箱【不该顺带】把网断掉 —— 隔离文件系统是一回事,断网是另一回事。

    2026-08-15 当事人抓到的实现偏离:我把 SANDBOX_NETWORK 默认写成了 "none",
    于是「打开沙箱」= 「顺带断网」,而设计说的是「bench 断网、平时用有网」。
    🪝 又一次「默认值 = 最少惊讶的那个」—— 刚讲完这条规矩,
       转头就在旁边那个开关上违反了它。规矩容易讲,落到每个开关上很容易漏。
    """
    out = sbx.run_bash(NET_PROBE)
    assert "Traceback" not in out, f"沙箱默认把网断了 —— 这是副作用不是设计:{out[:200]}"


def test_without_sandbox_the_escape_actually_works(trunk, tmp_path, monkeypatch):
    """🔴 双头验的另一头:【不开沙箱】时,那次越界是真的会成功的。

    为什么这条不可省:上面两条「越界失败」有一种【假绿】的可能 ——
    万一容器压根没起来、docker exec 直接报错,文件同样不会被创建,
    测试照样绿,但挡住它的不是隔离,是「命令根本没跑」。

    这条把「漏洞真实存在」钉死:同样的命令,关掉沙箱就写成功。
    于是上面那两条的绿,只能由沙箱来解释。
    🪝 跟堵判分漏洞时一样:先证明漏洞是真的,再证明修复是有效的。

    (这也是 2026-08-12 T19 自测的原样复刻 —— 模型当时的原话:"I'll use bash instead"。)
    """
    monkeypatch.setattr(trunk, "WORKDIR", tmp_path)
    monkeypatch.setattr(trunk, "SANDBOX_MODE", "off")
    outside = tmp_path.parent / "escape_proof.txt"
    if outside.exists():
        outside.unlink()
    trunk.run_bash(f"echo pwned > {outside}")
    assert outside.exists(), "不开沙箱居然也没写成功?那上面两条测的就不是沙箱"
    assert "pwned" in outside.read_text()
    outside.unlink()


def test_stop_sandbox_is_idempotent(trunk):
    """没有容器时调 stop_sandbox 不许报错 —— 它被放在 finally 里,
    而 finally 是【任何情况都会跑】的地方,包括「压根没开过沙箱」那种情况。
    🪝 收尾函数必须能在「没什么可收」的时候安静返回。
    """
    trunk._sandbox_name = None
    trunk.stop_sandbox()  # 不抛异常即通过
    trunk.stop_sandbox()  # 连调两次也不行出错


@needs_docker
def test_sandbox_hides_secrets(trunk, tmp_path, monkeypatch):
    """🔴 API key 不许被盒子读到 —— 但它就躺在工作区里,沙箱的挂载范围【内】。

    2026-08-15 实测发现:WORKDIR 是项目根,.env 就在那儿,
    agent 一句 `cat .env` 就读到 key,【沙箱开着也一样】——
    因为隔离挡的是「工作区外面」,而 .env 在里面。
    🪝 隔离画的是一条线,线【里面】的东西它一概不管 —— 别把「装了沙箱」当成「安全了」。

    做法:拿 /dev/null 盖在这些文件上,盒子里它们存在但是空的。
    """
    monkeypatch.setattr(trunk, "WORKDIR", tmp_path)
    monkeypatch.setattr(trunk, "SANDBOX_MODE", "docker")
    monkeypatch.setattr(trunk, "_sandbox_name", None)
    (tmp_path / ".env").write_text("API_KEY=sk-super-secret-value\n")
    (tmp_path / "normal.txt").write_text("这是普通文件,不该被盖住\n")
    try:
        assert "sk-super-secret" not in trunk.run_bash("cat .env")
        assert "空的" in trunk.run_bash("test -s .env && echo 有内容 || echo 空的")
        # 反面:普通文件不许被误伤 —— 盖太多等于把工作区废了
        assert "这是普通文件" in trunk.run_bash("cat normal.txt")
    finally:
        trunk.stop_sandbox()
    # 宿主机上的真文件必须完好 —— 盖住的只是盒子里的视图,不是把你的 .env 清空了
    assert "sk-super-secret-value" in (tmp_path / ".env").read_text()


@needs_docker
def test_hiding_does_not_create_missing_files(trunk, tmp_path, monkeypatch):
    """⚠️ 只盖【真实存在】的文件。

    docker 遇到不存在的挂载路径会【自动创建】它 —— 不判断的话,
    每个考场里都会凭空多出一个空 .env,污染 bench 的「改动了几行」统计。
    """
    monkeypatch.setattr(trunk, "WORKDIR", tmp_path)
    monkeypatch.setattr(trunk, "SANDBOX_MODE", "docker")
    monkeypatch.setattr(trunk, "_sandbox_name", None)
    assert not (tmp_path / ".env").exists()
    try:
        trunk.run_bash("echo hi")
    finally:
        trunk.stop_sandbox()
    assert not (tmp_path / ".env").exists(), "凭空造出了一个 .env —— 会污染考场"

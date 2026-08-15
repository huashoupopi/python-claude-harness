# agent 的 bash 关进这个盒子里跑。
#
# 为什么要自己 build 而不是直接用 python:3.13-slim:
#   考场要跑 pytest,而沙箱是 --network none(断网)的 —— 容器里 pip install 不了,
#   必须【预装进镜像】。
#   🪝 这正是隔离的连带成本:断网之后,凡是「跑的时候现装」的东西全部失效,
#      依赖必须提前固化。
FROM python:3.13-slim
RUN pip install --no-cache-dir pytest==9.1.1
# sleep infinity:让容器活着等 docker exec —— 容器的生命周期 = 一个隔离单元
# (一场考试 / 一次会话),不是一条命令。
CMD ["sleep", "infinity"]

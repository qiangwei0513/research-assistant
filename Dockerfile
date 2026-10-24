# 赛题一科研助手 Agent 镜像
#
# 交付要求（已核对官方赛题详情与基础镜像归档）：
#   Linux/AMD64、非 root 运行、声明 8080/tcp、提供 GET /health 与 POST /solve、
#   工作目录 /output、保留 curl（基础镜像的 HEALTHCHECK 依赖它）、覆盖默认 CMD。
#
# 基础镜像已预装全部运行时依赖（fastapi 0.111.0 / starlette 0.37.2 / pydantic 2.8.0 /
# uvicorn 0.30.0 / httpx 0.27.0 / anyio 4.15.1，Python 3.11.16），因此这里只补
# 镜像中确实缺失的 pypdf，并用 --no-deps 避免扰动已锁定的版本集合。
# pytest / ruff 是 dev 依赖，绝不进镜像。

FROM agenthack/base:1.1.1

USER root

# /output 在基础镜像里不存在；平台的评测结果、报告与图片都写在这里。
# 必须以 agent(uid=1000) 所有，否则非 root 运行时 /health 的可写探测会失败，
# 基础镜像 HEALTHCHECK 连续 6 次失败会导致整轮评测起不来。
RUN mkdir -p /output && chown -R 1000:1000 /output

# 镜像内没有任何 PDF 解析库；pypdf 是纯 Python、零强制依赖，
# 因此不需要平台专属 wheel，从任意构建主机装出来的结果都一致。
RUN pip install --no-cache-dir --no-deps pypdf==6.21.2

WORKDIR /app
COPY src/ /app/src/

# 显式模块路径：工作目录随后会切到 /output，靠 PYTHONPATH 保证仍可导入，
# 不依赖把包安装进 site-packages（那样还需要构建后端）。
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER agent
WORKDIR /output

EXPOSE 8080

# 必须覆盖基础镜像那条故意 exit 1 的默认 CMD
CMD ["python", "-m", "research_assistant"]

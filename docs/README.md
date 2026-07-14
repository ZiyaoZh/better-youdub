# better-youdub Migration Docs

本目录记录 better-youdub 从 `/tmp/YouDub2026` Windows 版工作流迁移到 Linux + 容器部署的评估、设计和实施规范。旧仓库只作为迁移参考，不直接修改其代码。

## 文档索引

- [现状评估](./assessment.md)
- [迁移与改造 SOP](./migration-sop.md)
- [翻译阶段设计](./translation-design.md)
- [容器部署决策](./container-strategy.md)
- [依赖与 Dockerfile 同步规范](./dependency-sync.md)

## 当前结论

1. `/tmp/YouDub2026` 是旧项目，只作为迁移参考；新项目应在 `/workspace` 下重新组织。
2. 当前运行中的容器适合作为“规划与轻量开发环境”，不适合作为最终运行镜像。
3. 后续应新增独立 app Dockerfile，使用 `nvidia/cuda` 或 PyTorch CUDA 基础镜像构建运行时镜像。
4. 任何新增或修改 Python/系统依赖，必须先更新依赖清单，再由 Dockerfile 从清单安装，避免手工进容器安装后丢失。
5. 当前 `/workspace/Dockerfile` 和 `/workspace/command.md` 存在明文密钥/Token 痕迹；后续镜像与文档必须改为 `.env`、Docker secret 或运行时环境变量注入。
6. 旧仓库实现存在多处历史包袱；后续开发以功能等价和 Linux/容器友好为目标，不需要严格沿用旧实现方式。
7. 每次新增或修改功能，都必须同步更新 README、SOP 和对应 smoke/Docker 验证命令。

## 固定测试素材

- 测试视频标识：`https://www.youtube.com/watch?v=6o68Fg2-bhM`
- 该链接仅作为测试素材来源标识。基础框架不实现自动抓取、反自动化绕过或 cookie 刷新；测试时应使用已经合法取得并放入本地测试目录的视频文件。

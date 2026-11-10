# GPU 容器运行一段时间后 CUDA 消失

如果 worker 报 `CUDA was requested but no CUDA device is available`，先检查同一个容器：

```bash
docker compose exec youdub-gpu nvidia-smi
docker compose exec youdub-gpu python -c 'import os; os.close(os.open("/dev/nvidiactl", os.O_RDWR))'
```

`Failed to initialize NVML: Unknown Error` 加上打开设备返回 `Operation not permitted`
说明设备访问权限出了问题。显存不足通常不会让 `/dev/nvidiactl` 的打开操作返回 EPERM。
在使用 systemd cgroup 驱动和 NVIDIA legacy hook 的 Docker 环境中，设备由 hook
注入，但不在 Docker 的 `HostConfig.Devices` 中；systemd/cgroup 更新可能丢失这部分
访问权限。容器重启重新注入权限，因此暂时恢复。具体触发事件需要宿主机 journal
确认，单凭 NVML 错误不能排除驱动等其他故障。

为避免该问题，保留 `gpus: all` 提供驱动库，同时显式声明 GPU、控制和 UVM
字符设备，使 Docker 能在更新 cgroup 时重新应用权限。下面的生成器自动发现 GPU
数量和可选的 `nvidia-caps`，不写死 UVM 的 major number，也不需要 privileged。

在 GPU 宿主机项目目录执行（宿主机需有 Python 3）：

```bash
python3 scripts/gpu_device_mappings.py > compose.gpu-devices.local.yml.tmp
mv compose.gpu-devices.local.yml.tmp compose.gpu-devices.local.yml
```

如果当前终端是没有 NVIDIA 设备的开发容器，可以从现有 GPU 服务读取设备列表。
即便 GPU 服务已失去设备访问权限，读取设备节点属性仍然可用：

```bash
docker compose -f compose.gpu.yml -f compose.dns.yml exec -T youdub-gpu python - \
  < scripts/gpu_device_mappings.py > compose.gpu-devices.local.yml.tmp
mv compose.gpu-devices.local.yml.tmp compose.gpu-devices.local.yml
```

将本地覆盖文件加入 `.env` 中现有的 `COMPOSE_FILE`，保留已有 DNS 等覆盖文件：

```dotenv
COMPOSE_FILE=compose.gpu.yml:compose.dns.yml:compose.gpu-devices.local.yml
```

然后在没有运行中任务时重建服务（`restart` 不会应用新设备映射）：

```bash
docker compose config --quiet
docker compose up -d --force-recreate --no-build youdub-gpu
docker compose exec youdub-gpu nvidia-smi
docker compose exec youdub-gpu python -c 'import torch; assert torch.cuda.is_available(); [(torch.empty(1, device=f"cuda:{i}"), torch.cuda.synchronize(i)) for i in range(torch.cuda.device_count())]; print("all CUDA devices OK")'
```

若命令显式传入 `-f`，也必须带上设备覆盖文件，否则会忽略 `.env` 的
`COMPOSE_FILE`。设备覆盖文件与机器相关，不提交到 Git；GPU 数量或驱动设备节点
变化后重新生成并重建容器。此修复不需要修改宿主机全局 cgroup 配置。

故障 worker 本身已经是独立 Python 进程。容器级设备权限丢失时，再启动 worker、
换卡或增加重试次数均不能恢复权限。界面父进程可能仍保留先前 CUDA 初始化结果，
所以仍会选出 `cuda:2`，而新的 worker 初始化 CUDA 时会失败。

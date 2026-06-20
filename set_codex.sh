#!/bin/bash

# 用法: ./update_config_advanced.sh <vendor> <new_api_key>
# vendor 可选: packy, rawchat, anyrouter, sub2api

set -e

CONTAINER_NAME="yd_instance"
TMP_CONFIG="./config.toml.tmp"
TMP_AUTH="./auth.json.tmp"
CONTAINER_CONFIG_PATH="/root/.codex/config.toml"
CONTAINER_AUTH_PATH="/root/.codex/auth.json"

cleanup() {
    rm -f "$TMP_CONFIG" "$TMP_AUTH"
    echo "临时文件已清理。"
}
trap cleanup EXIT

if [ $# -ne 2 ]; then
    echo "错误：需要两个参数"
    echo "用法: $0 <vendor> <new_api_key>"
    echo "vendor 可选: packy, rawchat, anyrouter, sub2api"
    exit 1
fi

VENDOR="$1"
NEW_API_KEY="$2"

# 供应商配置
case "$VENDOR" in
    packy)
        PROVIDER_VALUE="packycode"
        SECTION="packycode"
        BASE_URL="https://www.packyapi.com/v1"
        NAME="packycode"
        ;;
    anyrouter)
        PROVIDER_VALUE="anyrouter"
        SECTION="anyrouter"
        BASE_URL="https://a-ocnfniawgw.cn-shanghai.fcapp.run/v1"
        NAME="Any Router"
        ;;
    rawchat)
        PROVIDER_VALUE="rawchat"
        SECTION="rawchat"
        BASE_URL="https://new.sharedchat.cc/codex"
        NAME="rawchat"
        ;;
    sub2api)
        PROVIDER_VALUE="sub2api"
        SECTION="sub2api"
        BASE_URL="http://222.20.126.109:18080"
        NAME="sub2api"
        ;;
    *)
        echo "错误：不支持的供应商 '$VENDOR'"
        exit 1
        ;;
esac

echo ">>> 供应商: $VENDOR -> model_provider = $PROVIDER_VALUE"
echo ">>> 设置 API Key: ${NEW_API_KEY:0:10}..."

# 从容器复制文件
echo ">>> 从容器复制配置文件..."
docker cp "$CONTAINER_NAME:$CONTAINER_CONFIG_PATH" "$TMP_CONFIG"
docker cp "$CONTAINER_NAME:$CONTAINER_AUTH_PATH" "$TMP_AUTH"

# ========== 使用 Python 修改 config.toml ==========
echo ">>> 使用 Python 修改 config.toml ..."
python3 << EOF
import re
import sys

# 读取原始配置
with open("$TMP_CONFIG", "r") as f:
    lines = f.readlines()

# 我们需要修改/添加的内容
new_provider = "$PROVIDER_VALUE"
new_section = "$SECTION"
new_base_url = "$BASE_URL"
new_name = "$NAME"

# 结果行列表
new_lines = []
i = 0
provider_modified = False
section_found = False
in_target_section = False
project_section_modified = False

while i < len(lines):
    line = lines[i]
    # 1. 修改顶层 model_provider
    if not provider_modified and re.match(r'^model_provider\s*=', line):
        new_lines.append(f'model_provider = "{new_provider}"\n')
        provider_modified = True
        i += 1
        continue

    # 2. 处理项目节（[projects."..."]) 中的 preferred_auth_method
    if re.match(r'^\[projects\."', line):
        new_lines.append(line)
        i += 1
        # 查找该节内的 preferred_auth_method 并修改/添加
        j = i
        found_auth = False
        while j < len(lines) and not re.match(r'^\[', lines[j]):
            if re.match(r'^\s*preferred_auth_method\s*=', lines[j]):
                found_auth = True
                # 修改该行
                new_lines.append(f'preferred_auth_method = "apikey"\n')
                j += 1
                break
            else:
                new_lines.append(lines[j])
                j += 1
        if not found_auth:
            # 在项目节末尾添加
            new_lines.append('preferred_auth_method = "apikey"\n')
        # 继续复制剩余行直到下一个节
        while j < len(lines) and not re.match(r'^\[', lines[j]):
            new_lines.append(lines[j])
            j += 1
        i = j
        project_section_modified = True
        continue

    # 3. 处理 model_providers.<section> 节
    if re.match(r'^\[model_providers\.', line):
        # 检查是否是目标节
        if f'model_providers.{new_section}' in line:
            new_lines.append(line)   # 保留节头
            i += 1
            # 更新节内的 base_url 和 name
            while i < len(lines) and not re.match(r'^\[', lines[i]):
                l = lines[i]
                if re.match(r'^\s*base_url\s*=', l):
                    new_lines.append(f'base_url = "{new_base_url}"\n')
                elif re.match(r'^\s*name\s*=', l):
                    new_lines.append(f'name = "{new_name}"\n')
                else:
                    new_lines.append(l)
                i += 1
            section_found = True
            continue
        else:
            # 其他供应商节，原样保留
            new_lines.append(line)
            i += 1
            continue

    # 默认行
    new_lines.append(line)
    i += 1

# 如果顶层 model_provider 没有被修改（文件中不存在该行），则添加
if not provider_modified:
    new_lines.insert(0, f'model_provider = "{new_provider}"\n')

# 如果目标供应商节不存在，则追加
if not section_found:
    new_lines.append(f'\n[model_providers.{new_section}]\n')
    new_lines.append(f'base_url = "{new_base_url}"\n')
    new_lines.append(f'name = "{new_name}"\n')
    new_lines.append('requires_openai_auth = true\n')
    new_lines.append('wire_api = "responses"\n')

# 写回文件
with open("$TMP_CONFIG", "w") as f:
    f.writelines(new_lines)

print("config.toml 修改完成")
EOF

# ========== 修改 auth.json ==========
echo ">>> 修改 auth.json ..."
if command -v jq &> /dev/null; then
    jq --arg key "$NEW_API_KEY" '.OPENAI_API_KEY = $key' "$TMP_AUTH" > "${TMP_AUTH}.new"
    mv "${TMP_AUTH}.new" "$TMP_AUTH"
else
    # 使用 Python 作为后备（更可靠）
    python3 << EOF
import json
with open("$TMP_AUTH", "r") as f:
    data = json.load(f)
data["OPENAI_API_KEY"] = "$NEW_API_KEY"
with open("$TMP_AUTH", "w") as f:
    json.dump(data, f, indent=2)
EOF
fi

# 复制回容器
echo ">>> 将修改后的文件复制回容器..."
docker cp "$TMP_CONFIG" "$CONTAINER_NAME:$CONTAINER_CONFIG_PATH"
docker cp "$TMP_AUTH" "$CONTAINER_NAME:$CONTAINER_AUTH_PATH"

# 显示修改后相关行
echo ">>> 修改完成。关键配置摘要："
grep -E "^model_provider" "$TMP_CONFIG" || true
grep -A3 "\[model_providers\.$SECTION\]" "$TMP_CONFIG" || true
grep -B1 -A1 "preferred_auth_method" "$TMP_CONFIG" | head -5 || true
grep "OPENAI_API_KEY" "$TMP_AUTH" || true

echo ">>> 脚本执行成功。"
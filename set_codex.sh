#!/bin/bash

# 脚本功能：修改 yd_instance 容器中的 config.toml 和 auth.json
# 用法：./update_config.sh <new_base_url> <new_api_key>
# 示例：./update_config.sh "https://new.api.com/v1" "sk-abc123"

set -e  # 遇到错误立即退出

# 配置
CONTAINER_NAME="yd_instance"
CONFIG_FILE="config.toml"
AUTH_FILE="auth.json"
CONTAINER_CONFIG_PATH="/root/.codex/config.toml"
CONTAINER_AUTH_PATH="/root/.codex/auth.json"

# 临时文件（放在当前目录）
TMP_CONFIG="./${CONFIG_FILE}.tmp"
TMP_AUTH="./${AUTH_FILE}.tmp"

# 清理函数：删除临时文件
cleanup() {
    rm -f "$TMP_CONFIG" "$TMP_AUTH"
    echo "临时文件已清理。"
}

# 错误处理：确保即使脚本中断也删除临时文件
trap cleanup EXIT

# 参数检查
if [ $# -ne 2 ]; then
    echo "错误：需要两个参数"
    echo "用法: $0 <new_base_url> <new_api_key>"
    exit 1
fi

NEW_BASE_URL="$1"
NEW_API_KEY="$2"

echo ">>> 开始更新配置..."

# 1. 从容器复制文件到宿主机临时文件
echo ">>> 从容器复制 $CONFIG_FILE ..."
docker cp "$CONTAINER_NAME:$CONTAINER_CONFIG_PATH" "$TMP_CONFIG"
echo ">>> 从容器复制 $AUTH_FILE ..."
docker cp "$CONTAINER_NAME:$CONTAINER_AUTH_PATH" "$TMP_AUTH"

# 2. 修改 config.toml 中的 base_url
#    匹配行 base_url = "..." 或 base_url = '...' 或 base_url = ...（无引号）
#    替换为 base_url = "新值"
echo ">>> 修改 $CONFIG_FILE 中的 base_url 为 $NEW_BASE_URL"
sed -i "s|^\([[:space:]]*base_url[[:space:]]*=[[:space:]]*\).*|\1\"$NEW_BASE_URL\"|" "$TMP_CONFIG"

# 3. 修改 auth.json 中的 OPENAI_API_KEY 值
#    使用 jq 如果可用，否则使用 sed（后者较脆弱）
if command -v jq &> /dev/null; then
    echo ">>> 使用 jq 修改 $AUTH_FILE"
    jq --arg key "$NEW_API_KEY" '.OPENAI_API_KEY = $key' "$TMP_AUTH" > "${TMP_AUTH}.jqtmp"
    mv "${TMP_AUTH}.jqtmp" "$TMP_AUTH"
else
    echo ">>> 警告：jq 未安装，使用 sed 进行简单替换（可能不严谨）"
    # 此方法假设 JSON 格式为 "OPENAI_API_KEY": "旧值"
    sed -i "s|\"OPENAI_API_KEY\"[[:space:]]*:[[:space:]]*\"[^\"]*\"|\"OPENAI_API_KEY\": \"$NEW_API_KEY\"|" "$TMP_AUTH"
fi

# 4. 将修改后的文件复制回容器
echo ">>> 复制修改后的 $CONFIG_FILE 回容器..."
docker cp "$TMP_CONFIG" "$CONTAINER_NAME:$CONTAINER_CONFIG_PATH"
echo ">>> 复制修改后的 $AUTH_FILE 回容器..."
docker cp "$TMP_AUTH" "$CONTAINER_NAME:$CONTAINER_AUTH_PATH"

# 5. 验证（可选）: 显示修改后的内容片段
echo ">>> 修改完成。新配置摘要："
echo "--- config.toml base_url ---"
grep "^[[:space:]]*base_url" "$TMP_CONFIG" || echo "未找到 base_url 行"
echo "--- auth.json OPENAI_API_KEY ---"
grep "OPENAI_API_KEY" "$TMP_AUTH" || echo "未找到 OPENAI_API_KEY 字段"

# 清理会在 exit 时由 trap 自动执行
echo ">>> 脚本执行成功。"
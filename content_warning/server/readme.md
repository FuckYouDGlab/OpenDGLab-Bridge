# 前方高能 (DG-LAB 游戏联动中间件)

这是一个 Python Flask 服务器脚本，旨在作为一个中间件，连接游戏 Mod 和 DG-LAB 设备的 HTTP API。

## 功能

*   **接收游戏数据:** 通过一个简单的 HTTP 端点 (`/update_stamina`) 接收来自游戏 Mod 发送的实时玩家耐力值 (Stamina)。
*   **强度计算:**
    *   根据接收到的耐力值，反向计算基础设备强度 (耐力越低，基础强度越高)。
    *   实现了一个"惩罚"机制：每次玩家耐力归零时，惩罚值会增加一个固定量。
    *   惩罚值会随时间逐渐衰减（当玩家体力不为零时）。
    *   最终强度 = 基础强度 * (1 + 惩罚值)，并应用上下限。
*   **数据缓冲与平滑:**
    *   在设定的时间间隔内（例如 0.5 秒）收集所有接收到的耐力值。
    *   计算该时间间隔内的平均耐力值，用于强度计算，以平滑输出。
*   **条件性设备控制:**
    *   周期性地（例如每 0.5 秒）计算目标强度。
    *   只有当计算出的目标强度与上次成功发送给设备的值**不同**时，才会调用 DG-LAB HTTP API (`/control/strength`) 发送指令，以减少不必要的 API 调用。
*   **可配置性:** 脚本顶部的参数（如强度乘数、最大强度、惩罚增量、惩罚衰减速率、发送间隔、目标通道等）可以根据个人偏好和设备耐受度进行调整。

## 依赖

*   Python 3.x
*   Flask (`pip install Flask`)
*   requests (`pip install requests`)

## 配置参数

脚本开头定义了多个可配置参数，可以根据需要进行调整：

*   `MAX_STAMINA`: 游戏中耐力的最大值。
*   `STRENGTH_MULTIPLIER`: 基础强度计算的乘数。 `基础强度 = (MAX_STAMINA - 平均耐力) * STRENGTH_MULTIPLIER`。
*   `MAX_OUTPUT_STRENGTH`: 发送给 DG-LAB 设备的最大强度值上限。
*   `PENALTY_INCREMENT_ON_ZERO`: 每次耐力归零时，惩罚值增加多少。
*   `PENALTY_DECAY_PER_SECOND`: 当耐力不为零时，每秒钟惩罚值减少多少。
*   `TARGET_CHANNEL`: 控制 DG-LAB 设备的哪个通道 ('a' 或 'b')。
*   `DG_LAB_API_STRENGTH_URL`: DG-LAB 设备 HTTP API 的强度控制端点 URL。
*   `SEND_INTERVAL`: 计算平均耐力值并尝试向设备发送强度指令的时间间隔（秒）。

## 运行

1.  **安装依赖:** `pip install Flask requests`
2.  **配置参数:** 根据需要修改脚本开头的配置参数。
3.  **运行脚本:** `python zhanweifu.py`
4.  **确保 DG-LAB API 运行:** 确保控制 DG-LAB 设备的 HTTP API 服务（监听 `DG_LAB_API_STRENGTH_URL` 指定的地址和端口）正在运行。
5.  **启动游戏 Mod:** 确保游戏 Mod 配置正确，能够向 `http://<运行脚本的机器IP>:17553/update_stamina` 发送 POST 请求，请求体为包含 `currentStamina` 字段的 JSON 数据。

## 工作流程

游戏 Mod (C#) --> `/update_stamina` (Python/Flask) --> [缓冲/平均/计算强度/惩罚] --> DG-LAB HTTP API (`/control/strength`) --> DG-LAB 设备

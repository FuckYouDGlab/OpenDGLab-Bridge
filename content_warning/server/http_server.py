from flask import Flask, request, jsonify
import logging
import time
import requests # 导入 requests 库
import threading # 导入 threading 库
import statistics # 导入 statistics 库
from collections import deque # 导入 deque
import copy # 导入 copy

# --- 配置参数 ---
MAX_STAMINA = 10.0
MAX_HEALTH = 100.0 # <<< 新增：最大生命值
STRENGTH_MULTIPLIER = 2 # 强度乘数 (可用于体力和生命值)
MAX_OUTPUT_STRENGTH = 60  # 发送给设备的最终强度上限
PENALTY_INCREMENT_ON_ZERO = 0.4 # 每次 *体力* 归零时增加的惩罚值
PENALTY_DECAY_PER_SECOND = 0.0001 # 当 *体力* > 0 时，每秒惩罚值减少量
TARGET_CHANNEL = "a"       # 目标设备通道 ('a' 或 'b')
DG_LAB_API_STRENGTH_URL = "http://127.0.0.1:8081/control/strength"
DG_LAB_API_WAVEFORM_URL = "http://127.0.0.1:8081/control/waveform"
SEND_INTERVAL = 0.2        # 发送强度间隔（秒）

# --- 全局状态变量 ---
current_penalty = 0.0 # 体力惩罚值
is_at_zero = False    # 体力是否为零的状态标记
last_sent_strength = -1   # 上次发送给设备的强度值
current_health_value = MAX_HEALTH # <<< 新增：当前生命值
last_received_data_type = "stamina" # <<< 新增：上次接收的数据类型

# --- 线程安全相关 ---
stamina_buffer = deque()
buffer_lock = threading.Lock()
penalty_lock = threading.Lock() # 用于保护 is_at_zero, current_penalty
data_lock = threading.Lock()    # <<< 新增：用于保护 current_health_value, last_received_data_type

# 配置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- 辅助函数：调用 DG-LAB API ---
def set_dglab_strength(channel, strength):
    """向 DG-LAB API 发送设置强度的请求"""
    payload = {"channel": channel, "strength": strength}
    try:
        # 稍微增加超时时间，因为这是后台任务，影响较小
        response = requests.post(DG_LAB_API_STRENGTH_URL, json=payload, timeout=1.0)
        response.raise_for_status() # 如果状态码不是 2xx，则抛出异常
        logging.info(f"Successfully sent strength {strength} to channel {channel}. Response: {response.json()}")
        return True
    except requests.exceptions.Timeout:
        logging.error(f"Timeout occurred when sending strength to {DG_LAB_API_STRENGTH_URL}")
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending strength to {DG_LAB_API_STRENGTH_URL}: {e}")
        if e.response is not None:
            try:
                logging.error(f"DG-LAB API Response: {e.response.text}")
            except Exception:
                pass
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred during DG-LAB API call: {e}", exc_info=True)
        return False

# --- 辅助函数：调用 DG-LAB API 设置波形 ---
def set_dglab_waveform(channel, waveform_preset):
    """向 DG-LAB API 发送设置波形预设的请求"""
    payload = {"channel": channel, "preset": waveform_preset}
    try:
        response = requests.post(DG_LAB_API_WAVEFORM_URL, json=payload, timeout=1.0)
        response.raise_for_status()
        logging.info(f"Successfully set waveform preset '{waveform_preset}' for channel {channel}. Response: {response.json()}")
        return True
    except requests.exceptions.Timeout:
        logging.error(f"Timeout occurred when setting waveform preset at {DG_LAB_API_WAVEFORM_URL}")
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Error setting waveform preset at {DG_LAB_API_WAVEFORM_URL}: {e}")
        if e.response is not None:
            try:
                logging.error(f"DG-LAB API Response: {e.response.text}")
            except Exception:
                pass
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred during DG-LAB waveform API call: {e}", exc_info=True)
        return False

# --- 后台发送线程函数 ---
def periodic_sender():
    """
    后台线程函数，周期性地处理缓冲区，计算平均值，更新/应用惩罚或处理生命值，
    并在强度变化时调用 DG-LAB API。
    """
    global current_penalty, is_at_zero, last_sent_strength, current_health_value, last_received_data_type

    logging.info("Background sender thread started.")
    while True:
        try:
            time.sleep(SEND_INTERVAL)

            _last_received_type = "unknown"
            _current_health = MAX_HEALTH
            _avg_stamina = MAX_STAMINA
            _current_penalty_val = 0.0
            _is_stamina_at_zero = False

            # --- 安全地读取需要的数据 ---
            with data_lock:
                _last_received_type = last_received_data_type
                _current_health = current_health_value

            with penalty_lock:
                 _current_penalty_val = current_penalty
                 _is_stamina_at_zero = is_at_zero # 读取体力是否为零的状态

            with buffer_lock:
                stamina_values_in_interval = list(copy.copy(stamina_buffer))
                stamina_buffer.clear()
                if stamina_values_in_interval:
                    _avg_stamina = statistics.mean(stamina_values_in_interval)

            # --- 根据上次接收的数据类型，计算最终强度 ---
            final_strength_int = -1

            if _last_received_type == "stamina":
                # --- 体力模式逻辑 ---
                logging.debug(f"Mode: Stamina. Avg: {_avg_stamina:.2f}")
                # 衰减惩罚值 (如果体力不为零)
                with penalty_lock:
                    if not _is_stamina_at_zero and current_penalty > 0:
                        decay_amount = PENALTY_DECAY_PER_SECOND * SEND_INTERVAL
                        current_penalty -= decay_amount
                        current_penalty = max(0, current_penalty)
                        if current_penalty > 0:
                           logging.debug(f"Penalty decayed by {decay_amount:.3f}. New penalty: {current_penalty:.2f}")
                        else:
                           logging.debug(f"Penalty decayed to zero.")
                    _current_penalty_val = current_penalty # 重新读取可能已衰减的值

                # 计算强度
                base_strength = max(0, (MAX_STAMINA - _avg_stamina)) * STRENGTH_MULTIPLIER
                effective_penalty_multiplier = 1.0 + _current_penalty_val if _current_penalty_val > 0 else 1.0
                calculated_strength = base_strength * effective_penalty_multiplier
                final_strength = max(0, min(calculated_strength, MAX_OUTPUT_STRENGTH))
                final_strength_int = int(round(final_strength))
                logging.debug(f"Stamina Calculation -> Base: {base_strength:.2f}, Penalty Mult: {effective_penalty_multiplier:.2f} -> Calc: {calculated_strength:.2f} -> Final Int: {final_strength_int}")

            elif _last_received_type == "health":
                # --- 生命值模式逻辑 (简单反比) ---
                logging.debug(f"Mode: Health. Current: {_current_health:.2f}")
                # 计算强度
                base_strength = max(0, (MAX_HEALTH - _current_health)) * STRENGTH_MULTIPLIER
                # 无惩罚值应用
                calculated_strength = base_strength
                final_strength = max(0, min(calculated_strength, MAX_OUTPUT_STRENGTH))
                final_strength_int = int(round(final_strength))
                logging.debug(f"Health Calculation -> Base: {base_strength:.2f} -> Final Int: {final_strength_int}")
                # 注意：在生命值模式下，体力惩罚值不会衰减

            else:
                logging.warning(f"Unknown last received data type: {_last_received_type}. Skipping strength update.")
                continue

            # --- 检查是否需要发送 --- (统一检查 final_strength_int)
            should_send = False
            with penalty_lock: # 使用 penalty_lock 保护 last_sent_strength
                if final_strength_int != last_sent_strength:
                    should_send = True
                    _target_strength_to_send = final_strength_int
                else:
                     logging.debug(f"Strength {final_strength_int} hasn't changed since last send. Skipping.")

            # --- 发送 API 请求 (如果需要) ---
            if should_send:
                logging.info(f"Strength changed to {final_strength_int} (Mode: {_last_received_type}). Sending update...")
                success = set_dglab_strength(TARGET_CHANNEL, _target_strength_to_send)
                if success:
                    with penalty_lock:
                        last_sent_strength = _target_strength_to_send
        except Exception as e:
            logging.error(f"Error in background sender thread: {e}", exc_info=True)
            time.sleep(1)


# --- Flask 路由 --- (修改后处理两种数据类型)
@app.route('/update_data', methods=['POST']) # <<< 修改路由
def update_data():
    """
    接收来自游戏 Mod 的数据更新请求 (包含 dataType 和 value)，
    更新对应状态并将体力值放入缓冲区。
    """
    global current_penalty, is_at_zero, current_health_value, last_received_data_type # 声明修改全局变量

    if not request.is_json:
        logging.warning("Received non-JSON request")
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    # logging.debug(f"Received data: {data}")

    if 'dataType' not in data or 'value' not in data:
        logging.warning("Missing 'dataType' or 'value' in received data")
        return jsonify({"error": "Missing 'dataType' or 'value' field"}), 400

    try:
        data_type = str(data['dataType']).lower() # 转小写方便比较
        value = float(data['value'])
        # logging.debug(f"Parsed data - Type: {data_type}, Value: {value}")

        if data_type == "stamina":
            # --- 处理体力数据 ---
            # 更新惩罚状态
            with penalty_lock:
                stamina_threshold = 0.01
                if value <= stamina_threshold and not is_at_zero:
                    is_at_zero = True
                    current_penalty += PENALTY_INCREMENT_ON_ZERO
                    logging.info(f"Stamina dropped to zero. Penalty incremented by {PENALTY_INCREMENT_ON_ZERO}. New penalty: {current_penalty:.2f}")
                elif value > stamina_threshold and is_at_zero:
                    is_at_zero = False
                    logging.info(f"Stamina recovered. Current penalty remains at {current_penalty:.2f}")
            # 添加到缓冲区
            with buffer_lock:
                stamina_buffer.append(value)
            # 更新最后接收类型
            with data_lock:
                last_received_data_type = "stamina"

        elif data_type == "health":
            # --- 处理生命值数据 ---
            with data_lock:
                current_health_value = value
                last_received_data_type = "health"
            logging.debug(f"Health updated to: {value:.2f}")

        else:
            logging.warning(f"Received unknown dataType: {data_type}")
            return jsonify({"error": f"Unknown dataType: {data_type}"}), 400

        # 返回简单的成功响应
        return jsonify({"status": "received", "dataType": data_type}), 200

    except (ValueError, TypeError) as e:
        logging.error(f"Invalid value type: {data.get('value')}. Error: {e}")
        return jsonify({"error": "Invalid value type"}), 400
    except Exception as e:
        logging.error(f"An unexpected error occurred in update_data: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    # --- 启动后台发送线程 ---
    sender_thread = threading.Thread(target=periodic_sender, daemon=True)
    sender_thread.start()

    # --- 添加这部分来抑制 Flask 的 INFO 日志 ---
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.WARNING)
    # -------------------------------------------

    logging.info("Starting Flask server...")
    app.run(host='0.0.0.0', port=17553, debug=False)

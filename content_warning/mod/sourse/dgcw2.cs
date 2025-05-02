using BepInEx;
using HarmonyLib;
using UnityEngine;
using System.Reflection; // 需要引入 Reflection 命名空间
using DemoMod.Patches; // 需要 using 你的 Patches 命名空间
using System.Net.Http; // 添加 HttpClient
using System.Text;    // 添加 Encoding
using System.Threading.Tasks; // 添加 Task (用于异步)
using BepInEx.Configuration; // 添加 BepInEx 配置命名空间
using System; // 需要 using System; for StringComparison

namespace DemoMod
{
    [BepInPlugin("zzzzzyc.dglab.dgcw2", "dgcw2", "1.0.0")]
    public class Plugin : BaseUnityPlugin
    {
        // --- 添加静态配置项变量 ---
        public static ConfigEntry<string> FlaskApiUrlEntry { get; private set; }
        public static ConfigEntry<float> SendIntervalEntry { get; private set; }
        public static ConfigEntry<string> DataSourceEntry { get; private set; } // <<< 新增：数据源配置
        // -------------------------

        private void Awake()
        {
            // --- 绑定配置项 ---
            FlaskApiUrlEntry = Config.Bind<string>(
                "General", // 配置文件的区域名
                "FlaskApiUrl", // 配置项的键名
                "http://127.0.0.1:17553/update_data", // <<< 修改了默认URL，表示更新通用数据
                "用于接收游戏数据的 Python Flask API 的完整 URL。" // 描述
            );

            SendIntervalEntry = Config.Bind<float>(
                "General",
                "SendIntervalSeconds",
                0.1f, // 默认值 (0.1 秒)
                "向 Flask API 发送数据的时间间隔（秒）。更小的值意味着更频繁的更新，但会增加网络负载。"
            );

            DataSourceEntry = Config.Bind<string>( // <<< 绑定新的配置项
                "General",
                "DataSource",
                "Stamina", // 默认发送耐力值
                "选择要发送给 Python API 的数据源：'Stamina' 或 'Health'。"
            );
            // ------------------

            var harmony = new Harmony("zzzzzyc.dglab.dgcw2"); // <<< 使用正确的 Harmony ID

            Logger.LogInfo("Attempting to patch manually...");
            Logger.LogInfo($"Flask API URL set to: {FlaskApiUrlEntry.Value}");
            Logger.LogInfo($"Send Interval set to: {SendIntervalEntry.Value} seconds");
            Logger.LogInfo($"Data Source set to: {DataSourceEntry.Value}"); // <<< 记录数据源配置

            try
            {
                // 1. 获取目标类型 (PlayerController)
                var playerControllerType = typeof(PlayerController);
                if (playerControllerType == null)
                {
                    Logger.LogError("Failed to find PlayerController type!");
                    return;
                }
                Logger.LogInfo("Found PlayerController type.");

                // 2. 获取目标方法 (Update)
                // 因为 Update 是 private 的，需要指定 BindingFlags
                var originalMethod = playerControllerType.GetMethod("Update", BindingFlags.Instance | BindingFlags.NonPublic);
                if (originalMethod == null)
                {
                    Logger.LogError("Failed to find Update method in PlayerController!");
                    // 尝试查找 public 方法以防万一（虽然反编译显示是 private）
                    originalMethod = playerControllerType.GetMethod("Update", BindingFlags.Instance | BindingFlags.Public);
                    if (originalMethod == null)
                    {
                        Logger.LogError("Failed to find Update method (public or private)!");
                        return;
                    }
                }
                Logger.LogInfo("Found Update method.");

                // 3. 获取补丁方法 (PlayerDataMonitorPatch)
                var patchMethod = typeof(PlayerControllerPatch).GetMethod("PlayerDataMonitorPatch", BindingFlags.Static | BindingFlags.NonPublic);
                if (patchMethod == null)
                {
                    Logger.LogError("Failed to find PlayerDataMonitorPatch method!");
                    return;
                }
                Logger.LogInfo("Found PlayerDataMonitorPatch method.");

                // 4. 创建 HarmonyMethod 实例指向补丁方法
                var postfix = new HarmonyMethod(patchMethod);

                // 5. 执行 Patch
                harmony.Patch(originalMethod, postfix: postfix); // 指定为 Postfix

                Logger.LogInfo("Manual Harmony patch applied successfully!");
            }
            catch (System.Exception ex)
            {
                Logger.LogError($"Exception during manual patching: {ex}");
            }

            // 你可以暂时注释掉原来的 PatchAll
            // harmony.PatchAll();
            // Logger.LogInfo("Harmony patches applied."); // 这行也注释掉或移到 try 成功后
        }
    }
}
namespace DemoMod.Patches
{
    [HarmonyPatch(typeof(PlayerController))]
    internal class PlayerControllerPatch
    {
        private static float lastLogTime = 0f;
        // private static readonly float logInterval = 0.1f; // 移除硬编码值

        // --- 创建一个静态的 HttpClient 实例以供重用 ---
        // 这样可以提高性能并避免端口耗尽问题
        private static readonly HttpClient httpClient = new HttpClient();
        // private static readonly string flaskApiUrl = "http://127.0.0.1:17553/update_stamina"; // 移除硬编码值

        [HarmonyPatch("Update")]
        [HarmonyPostfix]
        private static void PlayerDataMonitorPatch(PlayerController __instance)
        {
            if (__instance == null) return;

            // --- 读取并发送数据 (带频率控制) ---
            if (Time.time >= lastLogTime + Plugin.SendIntervalEntry.Value)
            {
                float valueToSend = -1f; // 存储要发送的值
                string dataTypeToSend = "unknown"; // 存储数据类型
                bool readSuccess = false;

                // --- 读取数据的 try-catch 块 ---
                try
                {
                    // --- 获取 player 和 data 对象 (与之前相同) ---
                    FieldInfo playerFieldInfo = AccessTools.Field(typeof(PlayerController), "player");
                    if (playerFieldInfo == null) throw new System.Exception("Could not find 'player' field info");
                    object playerObject = playerFieldInfo.GetValue(__instance);
                    if (playerObject == null) throw new System.Exception("Player object is null");
                    var playerType = playerObject.GetType();

                    FieldInfo dataFieldInfo = AccessTools.Field(playerType, "data");
                    if (dataFieldInfo == null) throw new System.Exception($"Could not find 'data' field info in {playerType.Name}");
                    object dataObject = dataFieldInfo.GetValue(playerObject);
                    if (dataObject == null) throw new System.Exception("Player data object is null");
                    var dataType = dataObject.GetType();

                    // --- 根据配置读取数据 --- // <<< 主要修改点
                    string dataSource = Plugin.DataSourceEntry.Value;

                    if (dataSource.Equals("Health", StringComparison.OrdinalIgnoreCase))
                    {
                        // 读取生命值 (health 是 public float)
                        FieldInfo healthFieldInfo = AccessTools.Field(dataType, "health"); // 仍然用 AccessTools 保持一致性
                        if (healthFieldInfo == null) throw new System.Exception($"Could not find 'health' field info in {dataType.Name}");
                        valueToSend = (float)healthFieldInfo.GetValue(dataObject);
                        dataTypeToSend = "health";
                        readSuccess = true;
                    }
                    else // 默认或明确设置为 "Stamina"
                    {
                        // 读取耐力值 (currentStamina 是 public float)
                        FieldInfo staminaFieldInfo = AccessTools.Field(dataType, "currentStamina");
                        if (staminaFieldInfo == null) throw new System.Exception($"Could not find 'currentStamina' field info in {dataType.Name}");
                        valueToSend = (float)staminaFieldInfo.GetValue(dataObject);
                        dataTypeToSend = "stamina";
                        readSuccess = true;
                    }
                    // ------------------------ // <<< 修改结束

                }
                catch (System.Exception ex)
                {
                    BepInEx.Logging.Logger.CreateLogSource("dgcw2").LogError($"Error reading player data: {ex.Message}"); // <<< 修改日志源名称
                }

                // --- 发送 HTTP 请求 ---
                if (readSuccess)
                {
                    lastLogTime = Time.time;

                    // --- 打印日志 (确认值) ---
                    BepInEx.Logging.Logger.CreateLogSource("dgcw2").LogInfo($"Read Data - Type: {dataTypeToSend}, Value: {valueToSend}"); // <<< 修改日志

                    // --- 发送数据到 Python ---
                    Task.Run(async () =>
                    {
                        try
                        {
                            // 构建包含 dataType 和 value 的 JSON
                            string jsonPayload = $"{{\"dataType\": \"{dataTypeToSend}\", \"value\": {valueToSend.ToString(System.Globalization.CultureInfo.InvariantCulture)}}}"; // <<< 修改 Payload
                            StringContent content = new StringContent(jsonPayload, Encoding.UTF8, "application/json");

                            // 使用配置的 URL
                            HttpResponseMessage response = await httpClient.PostAsync(Plugin.FlaskApiUrlEntry.Value, content);

                            if (!response.IsSuccessStatusCode)
                            {
                                string errorContent = await response.Content.ReadAsStringAsync();
                                BepInEx.Logging.Logger.CreateLogSource("dgcw2").LogWarning($"Failed to send data to Flask API. Status: {response.StatusCode}, Response: {errorContent}"); // <<< 修改日志源名称
                            }
                        }
                        catch (HttpRequestException httpEx)
                        {
                            BepInEx.Logging.Logger.CreateLogSource("dgcw2").LogError($"HTTP request failed: {httpEx.Message}"); // <<< 修改日志源名称
                        }
                        catch (System.Exception ex)
                        {
                            BepInEx.Logging.Logger.CreateLogSource("dgcw2").LogError($"Error sending data update: {ex.Message}\n{ex.StackTrace}"); // <<< 修改日志源名称
                        }
                    });
                }
                else
                {
                     lastLogTime = Time.time;
                }
            }
        }
    }
}

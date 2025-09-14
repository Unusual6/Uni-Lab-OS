from typing import List, Dict, Any
import networkx as nx
import logging
# 复用你们的工具：日志与加液（内部会解析 "100 µL"/"50 uL" 等单位，并生成泵动作序列）
from .add_protocol import create_action_log, add_liquid_volume  # :contentReference[oaicite:3]{index=3} :contentReference[oaicite:4]{index=4}
logger = logging.getLogger(__name__)

def debug_print(message):
    """调试输出"""
    print(f"🏛️ [RUN_COLUMN] {message}", flush=True)
    logger.info(f"[RUN_COLUMN] {message}")

def generate_elisa_protocol(
    G: nx.DiGraph,
    plate: dict,                          # 目标孔板（Graph中的容器节点，形如 {"id": "plate_1", ...}）
    wells: str = "A1:H12",                # 作用孔区（全板
) -> List[Dict[str, Any]]:
    pbst_cycles: int = 3,                 # PBST 洗板循环
    pbst_volume_ul: float = 300,            # 每孔上样/吸液体积（µL）
    pbst_soak_s: int = 30,                # 每循环浸泡时间（秒）
    tmb_volume_ul: float = 100,             # 每孔 TMB 体积（µL）
    tmb_develop_min: int = 10,            # TMB 显色时间（分钟）
    stop_volume_ul: float = 50,             # 每孔 Stop 体积（µL，强酸）
    read_wavelengths: int = 450,  # 读数波长（主 450 nm，参考 570 nm）
    actions: List[Dict[str, Any]] = []
    agv_id: str = "zhixing_agv",
    arm_id: str = "zhixing_ur_arm",
    washer_id: str = "405_ls",
    reader_id: str = "synergy_h1",
    plate_id = plate["id"]

    # ============ 0) 开始 ============
    actions.append(create_action_log(f"ELISA 流程开始 → 目标孔板: {plate_id}", "🚀"))  # :contentReference[oaicite:5]{index=5}
    actions.append({"action_name": "wait", "action_kwargs": {"time": 0.5}})

    # ============ 1) 机械臂/AGV 取板 → 洗板机 ============
    actions += [
        create_action_log("AGV 导航至洗板机工位", "🤖"),
        {"device_id": agv_id, "action_name": "send_nav_task", "action_kwargs": {"target": "WSH_DOCK"}},
        {"device_id": arm_id, "action_name": "move_pos_task",
         "action_kwargs": {"task_name": "camera/pick_plate.urp", "plate_id": plate_id}},
        {"device_id": arm_id, "action_name": "move_pos_task",
         "action_kwargs": {"task_name": "camera/place_to_washer.urp", "plate_id": plate_id}},
        create_action_log("孔板已放入 405 LS 洗板机", "🧺"),
    ]

    # ============ 2) 洗板（PBST） ============
    # 这里将 PBST 作为洗液；若已建模 PBST 试剂瓶，add_protocol 的泵路线会自动从 PBST 容器转移（find_reagent_vessel）:contentReference[oaicite:6]{index=6}
    actions += [
        create_action_log(f"洗板：PBST 循环 {pbst_cycles} 次，每孔 {pbst_volume_ul} µL，浸泡 {pbst_soak_s}s", "🚿"),
        {"device_id": washer_id,
         "action_name": "wash_plate", 
            "action_kwargs": {
                "plate": plate_id,
                "wells": wells,
                "buffer": "PBST",
                "cycles": pbst_cycles,
                "aspirate_volume_ul": pbst_volume_ul,
                "dispense_volume_ul": pbst_volume_ul,
                "soak_time_s": pbst_soak_s,
                "shake": True,
                "shake_speed_rpm": 600
            }},
        create_action_log("洗板完成", "✅"),
    ]

    # ============ 3) 加 TMB 显色底液 ============
    # 使用你们的 add_liquid_volume（内部会调用 generate_add_protocol → pump 协议、统一日志风格与单位解析）:contentReference[oaicite:7]{index=7}:contentReference[oaicite:8]{index=8}
    # 这里按“每孔 tmb_volume_ul µL”填加；总量=按需在泵侧计算
    actions.append(create_action_log(f"加 TMB：每孔 {tmb_volume_ul} µL → {wells}", "🧪"))
    actions += add_liquid_volume(  # 返回一串泵/等待/日志动作
        G=G,
        vessel=plate,               # 目标容器=孔板
        reagent="TMB",
        volume=f"{tmb_volume_ul} uL",
        time="1",                   # 允许内部默认速率或按泵策略
        rate_spec=""                # 可改为 "dropwise" 表示慢速（函数内置该模式）:contentReference[oaicite:9]{index=9}
    )

    # 显色孵育
    actions += [
        create_action_log(f"TMB 显色孵育 {tmb_develop_min} min（避光）", "⏳"),
        {"action_name": "wait", "action_kwargs": {"time": tmb_develop_min * 60}}
    ]

    # ============ 4) 加 Stop 终止液（强酸） ============
    actions.append(create_action_log(f"⚠️ 加 Stop（强酸）：每孔 {stop_volume_ul} µL → {wells}，启用酸液安全节拍", "🛑"))
    actions += add_liquid_volume(
        G=G,
        vessel=plate,
        reagent="Stop",
        volume=f"{stop_volume_ul} uL",
        time="1",         # 允许内部默认；必要时可指定更慢速率
        rate_spec=""      # 酸液可保持常速，避免溅射；如需超慢可用 "dropwise" :contentReference[oaicite:10]{index=10}
    )
    actions += [
        {"action_name": "wait", "action_kwargs": {"time": 5}},  # 终止反应后短暂停留稳定气泡/液面
        create_action_log("终止反应完成", "✅")
    ]

    # ============ 5) 机械臂/AGV 取板 → 酶标仪 ============
    actions += [
        create_action_log("AGV 导航至酶标仪工位", "🤖"),
        {"device_id": agv_id, "action_name": "send_nav_task", "action_kwargs": {"target": "H1_DOCK"}},
        {"device_id": arm_id, "action_name": "move_pos_task",
         "action_kwargs": {"task_name": "camera/pick_from_washer.urp", "plate_id": plate_id}},
        {"device_id": arm_id, "action_name": "move_pos_task",
         "action_kwargs": {"task_name": "camera/place_to_reader.urp", "plate_id": plate_id}},
        create_action_log("孔板已放入 SYNERGY H1", "📥"),
    ]

    # ============ 6) 酶标仪读数 ============
    actions += [
        create_action_log(f"读数：主波长 {read_wavelengths[0]} nm"
                          + (f"，参考 {read_wavelengths[1]} nm" if len(read_wavelengths) > 1 else ""), "📈"),
        {"device_id": reader_id, "action_name": "read_absorbance", "action_kwargs": {
            "plate": plate_id,
            "wells": wells,
            "wavelengths_nm": read_wavelengths,
            "read_speed": "normal",
            "shake_before_read": True,
            "shake_time_s": 5
        }},
        {"device_id": reader_id, "action_name": "export_data", "action_kwargs": {
            "format": "csv",
            "path": f"results/{plate_id}_ELISA.csv"
        }},
        create_action_log("读数完成并已导出数据", "✅"),
    ]

    # ============ 7) 收尾（可选：将孔板送回/废弃） ============
    actions += [
        {"device_id": arm_id, "action_name": "move_pos_task",
         "action_kwargs": {"task_name": "camera/take_out_reader.urp", "plate_id": plate_id}},
        create_action_log("ELISA 全流程完成", "🎉")
    ]
    return actions

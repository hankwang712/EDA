from fastmcp import FastMCP
import os
from mcp.server.fastmcp import Context
import re
import requests
import time
import math
import pandas as pd
from typing import Optional,Dict,Any,List,Tuple
from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field


EARTH_RADIUS = 6371.0
df1 = pd.read_excel("poi.xlsx",dtype={"NEW_TYPE": str})
df2 = pd.read_excel("AMap_adcode_citycode.xlsx")
def register_plan_tools(mcp:FastMCP):
    gaode_api_key = os.environ.get('GAODE_API_KEY')    
    def _get_poi(df, event):
        return str(df.loc[df['小类'] == event, 'NEW_TYPE'].values[0]) 

    def _get_citycode(df, cityname):
        matched = df[df['中文名'].str.contains(cityname, na=False)]
        if matched.empty:
            raise ValueError(f"城市名 '{cityname}' 未在行政编码表中找到")
        return str(matched.iloc[0]['adcode']) 
        
    def _fetch_poi_types(type_name: str, df1, gaode_api_key, location_str, radius=50000) -> str:
        """
        给定一个 POI 类型列表，依次获取每个类型的 POI 查询结果，并格式化返回。
        所有逻辑封装在一个函数中，无需额外调用子函数。
        """
        results = []
        try:
            type_code = _get_poi(df1, type_name)
            result = _get_poi_response(gaode_api_key, type_code, location_str, radius)
            time.sleep(0.1)
            if isinstance(result, dict) and result.get("results"):
                formatted = f"【{type_name}】\n{result}"
                results.append(formatted)
        except Exception as e:
            result = f"【{type_name} 查询失败: {e}】"
        return results
    
    def _get_poi_response(gaode_api_key, poi_type, location_str, radius):
        try:
            all_results = []
            page_num = 1
            max_page = 8  # page_size 设置为 25 时，最多查询 8 页

            while page_num <= max_page:
                response = requests.get(
                    "https://restapi.amap.com/v5/place/around",
                    params={
                        "key": gaode_api_key,
                        "types": poi_type,
                        "location": location_str,
                        "radius": radius,
                        "page_size": 25,
                        "sortrule": "distance",
                        "page_num": page_num
                    }
                )
                response.raise_for_status()
                data1 = response.json()

                if data1.get("status") != "1":
                    return {"error": f"高德查询失败: {data1.get('info', '')} (code: {data1.get('infocode', '')})"}

                pois = data1.get("pois", [])
                if not pois:
                    break 

                for result in pois:
                    if result.get("typecode") == "090100":
                        all_results.append({
                            "名称": result.get("name"),
                            "位置": result.get("location"),
                            "地址": result.get("address")
                            # "距离": result.get("distance"),
                        })

                page_num += 1
                time.sleep(0.2)  
            return {"results": all_results}

        except requests.exceptions.RequestException as e:
            return {"error": f"网络请求失败: {str(e)}"}
        except Exception as e:
            return {"error": f"未知错误: {str(e)}"}

    def _get_lon_lat(address: str, city: Optional[str] = None) -> str:
        """
        通过输入一个较为准确的地址信息，返回该地址对应的经纬度坐标。
        """
        params = {
            'address': address,
            'key': gaode_api_key,
        }
        url = f'https://restapi.amap.com/v3/geocode/geo'
        response = requests.get(url, params=params)
        data = response.json()
        if data['status'] == '1': 
            geocodes = data.get("geocodes", [])
            if geocodes:
                location = geocodes[0].get("location")
                return location  
        return None
    def destination_point(lon: float, lat: float, distance_km: float, bearing_deg: float) -> Tuple[float, float]:
        """
        计算从某点出发，沿球面方向前进 distance_km 千米后的坐标点。
        """
        bearing_rad = math.radians(bearing_deg)
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)

        angular_distance = distance_km / EARTH_RADIUS

        lat2_rad = math.asin(math.sin(lat_rad) * math.cos(angular_distance) +
                            math.cos(lat_rad) * math.sin(angular_distance) * math.cos(bearing_rad))

        lon2_rad = lon_rad + math.atan2(math.sin(bearing_rad) * math.sin(angular_distance) * math.cos(lat_rad),
                                        math.cos(angular_distance) - math.sin(lat_rad) * math.sin(lat2_rad))

        lat2 = math.degrees(lat2_rad)
        lon2 = math.degrees(lon2_rad)
        return lon2, lat2
    def _maps_distance(origins: str, destination: str, type: str = "1") -> Dict[str, Any]:
        """
        获取两地之间的距离
        """
        try:
            response = requests.get(
                "https://restapi.amap.com/v3/distance",
                params={
                    "key": gaode_api_key,
                    "origins": origins,
                    "destination": destination,
                    "type": type
                }
            )
            response.raise_for_status()
            data = response.json()
            if data["status"] != "1":
                return {"error": f"Direction Distance failed: {data.get('info') or data.get('infocode')}"}
            results = [{"distance": result.get("distance"), "duration": result.get("duration")} 
                        for result in data["results"]]
            return {"results": results}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}"}

    def _get_around_8_points(address: str, city: Optional[str] = None) -> List[Tuple[float, float]]:
        """
        获取指定地址为中心点，沿球面方向计算其周围 8 个方向（正北、东北、正东、东南、正南、西南、正西、西北）
        指定距离范围内的经纬度坐标点。

        参数：
            address (str): 必填。用于查询坐标的详细地址信息。
            city (Optional[str]): 可选。所属城市名称，可提升地址解析精度。
            distance_km (float): 可选。距离中心点的半径，单位为千米。默认为 50 千米。

        返回：
            List[Tuple[float, float]]: 共 8 个方向的 (经度, 纬度) 坐标列表，按正北起顺时针排列。
        
        注意：
            - 本函数使用球面余弦公式，确保在地球曲面上获得精确的方向点。
            - 返回的坐标可用于范围分析、POI 检索、救援调度等地理应用场景。
        """
        distance_km = 50.0
        location_str = _get_lon_lat(address, city)
        if not location_str:
            return []

        lon, lat = map(float, location_str.split(','))

        bearings = [0, 45, 90, 135, 180, 225, 270, 315]  # 方位角：北→顺时针
        points = [destination_point(lon, lat, distance_km, b) for b in bearings]
        return points
   
    def _get_around_poi_distribution(address: str, city: Optional[str] = None, poi_radius: int = 50000) -> dict:
        """
        获取指定地址周边 8 个方向的 POI 分布摘要（如医疗资源），每个方向按设定半径搜索。

        参数：
            address (str): 指定的详细地址信息。
            city (Optional[str]): 所在城市名称。
            poi_radius (int): 每个方向点的 POI 搜索半径，单位为米，默认为 50000。

        返回：
            dict: 格式为 {"正北": "xxx摘要", "正东": "xxx摘要", ...}
        """
        try:
            points = _get_around_8_points(address, city)
            directions = ["正北", "东北", "正东", "东南", "正南", "西南", "正西", "西北"]
        except Exception as e:
            return {"error": f"获取8方向点失败: {e}"}

        result_summary = {}

        for i, (lon, lat) in enumerate(points):
            direction_name = directions[i]
            location_str = f"{lon:.4f},{lat:.4f}"
            try:
                raw_results = _fetch_poi_types(
                    df1[df1['大类'] == '医疗保健服务']['小类'].tolist()[0], # 仅针对大型的医院
                    df1, gaode_api_key, location_str, radius=poi_radius
                )
                if not raw_results:
                    summary = "无有效结果"
                else:
                    summary = (
                        f"周边50km的综合医院包含：{raw_results}"
                    )

            except Exception as e:
                summary = f"查询失败: {e}"

            result_summary[direction_name] = summary
        return {"results": result_summary}
    def clean_backslashes(data):
        if isinstance(data, str):
            return re.sub(r"\\", "", data)
        elif isinstance(data, dict):
            return {k: clean_backslashes(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [clean_backslashes(i) for i in data]
        else:
            return data

    def _generate_schema_direction(address: str, city: Optional[str] = None, poi_radius: int = 50000) -> Dict:
        """
        根据各个方位的医院分布的信息进行结构化输出
        """
        class DirectionalHospital(BaseModel):
            """单个方向的医院信息"""
            医院的名字: str = Field(description="医院的完整名称")
            经纬度信息: str = Field(description="格式为 '经度,纬度' 的字符串（并且经度,纬度的小数位数为4位），例如 '120.1234,30.4569'")
            地址: str = Field(description="医院的详细地址")
        class POISummary(BaseModel):
            """八个方向的POI摘要，每个方向一个最权威的医院"""
            正北: DirectionalHospital
            东北: DirectionalHospital
            正东: DirectionalHospital
            东南: DirectionalHospital
            正南: DirectionalHospital
            西南: DirectionalHospital
            正西: DirectionalHospital
            西北: DirectionalHospital
        output_parser = JsonOutputParser(pydantic_object=POISummary)

        prompt = ChatPromptTemplate.from_messages([
            ("system", """
        你是一个专业的医疗信息分析与整理专家。你的任务是从一份关于某个地点周边八个方向医院分布的复杂文本中，提取并分析信息。

        请严格按照以下步骤执行：
        1.  **信息提取**：对于每个方向，从医院列表中提取出每家医院的名称、位置（经纬度）、距离和地址。
        2.  **权威性评估**：在每个方向的所有医院中，根据医院名称判断哪家最权威。判断优先级如下：
            - 一级优先：名称中包含“人民医院”、“中心医院”、“第一医院”、“中医院”、“大学附属医院”、“集团医院”的。
            - 二级优先：名称中包含“医院”但不含“门诊”、“分院”、“社区卫生服务中心”、“康复中心”、“专科医院”的。
            - 排除项：直接忽略名称中包含“(建设中)”或“(装修中)”的医院。
            - 平级处理：如果一个方向有多个一级优先的医院，选择名称最完整或最标准的那个（例如，“绍兴市人民医院” 优于 “人民医院”）。
        3.  **结构化输出**：为每个方向选择出最具代表性、最权威的那个医院，并将其信息按照指定的 JSON Schema 格式输出。

        输出格式必须严格遵循以下 JSON 结构，不要包含任何额外的说明、注释或 Markdown 代码块：
        {format_instructions}
        """),
            ("user", """{input_text}""")
        ])

        prompt_with_parser = prompt.partial(format_instructions=output_parser.get_format_instructions())
        llm = init_chat_model(model = "deepseek-chat", model_provider="deepseek")
        chain = prompt_with_parser | llm | output_parser

        try:
            destination_point = _get_lon_lat(address, city)
            raw_text = _get_around_poi_distribution(address, city, poi_radius)
            cleaned_text = clean_backslashes(raw_text)
            structured_result = chain.invoke({"input_text":cleaned_text})
            for direction, hospital in structured_result.items():
                try:
                    origin = hospital.get("经纬度信息", "").replace(" ", "")
                    if origin and destination_point:
                        hospital["距离"] = _maps_distance(origins=origin, destination=destination_point)
                        time.sleep(0.15)
                    else:
                        hospital["距离"] = "未知"
                except Exception as e:
                    hospital["距离"] = "错误"
                    return {'error':f"[警告] 方向 {direction} 计算距离失败：{e}"}
            # return json.dumps(structured_result, ensure_ascii=False, indent=2)
            return structured_result
        except Exception as e:
            return {'error':f"解析失败: {e}"}
    @mcp.tool()
    def get_route_summary(destination: str, avoidpolygons: list = []) -> Dict:
        """
        获取以指定地址为中心的多方向路径规划摘要（支持避让区域）。

        参数：
            destination (str): 目标中心地址，作为路径终点。例如："杭州电子科技大学下沙校区"。
            avoidpolygons（list，可选非必须）：用于指定避让区域的列表，每个避让区域由多个地点名称构成一个闭合多边形。

            格式说明：
            - 每个避让区域是一个地点名称列表，例如：["西湖", "之江", "滨江"]
            - 如果有多个区域，请用列表嵌套列表的方式表示，例如：
            [
                ["杭州电子科技大学", "二号大街", "浙江理工大学"],
                ["学林街", "文一西路", "文二西路"]
            ]

            约束条件：
            - 最多支持 32 个避让区域；
            - 每个区域最多包含 16 个地点（构成一个闭合多边形）；
            - 所有地点名称应为自然语言地理实体（如街道、学校、景点、行政区等）。

            示例：
            - 单个区域：["西湖", "之江", "滨江"]
            - 多个区域：[["浙江大学", "文新街道", "西溪湿地"], ["杭州东站", "德胜快速路", "九堡"]]

        返回：
            dict:
                - 成功时，返回：
                    {
                        "results": {
                            "正北": "从 XXX医院 到 destination 的路线总长约 XXXX 米，用时约 XXXX 秒。\n步骤列表...",
                            ...
                        }
                    }
                - 失败时，返回：
                    {"error": "错误信息"}

        功能说明：
            - 自动获取以目标地址为中心、半径50公里内八个方位（正北、东北、正东、东南、正南、西南、正西、西北）最具权威的医院。
            - 使用高德路径规划 API，计算每家医院到中心地址的最佳行车路线。
            - 支持设置避让区域，绕开用户指定的敏感区域或灾害区域。
            - 输出每个方向的路线总长度、预计用时，并提取关键导航步骤（忽略单段低于1000米的指令）。
        """
        def format_address_list(avoidpolygons: list) -> list:
            """
            将避让区域中的地名列表转换为经纬度坐标列表
            输入示例：[
                ["浙江大学", "文新街道", "西溪湿地"],
                ["杭州东站", "德胜快速路", "九堡"]
            ]
            输出示例：[
                [[110, 20], [113, 21], [112, 23]],
                [[112, 23], [111, 22], [111, 19]]
            ]
            """
            def _get_single_lon_lat(name: str) -> list:
                """
                模拟地名转经纬度的函数
                实际使用时应调用地图API（如高德、百度等）
                """
                # 这里只是 mock 示例，实际应替换为真实的地址解析函数
                import random
                return [round(random.uniform(110, 120), 6), round(random.uniform(20, 30), 6)]

            result = []
            for region in avoidpolygons:
                if not region or len(region) < 3:
                    continue  # 忽略不合法区域
                coord_list = []
                for place in region:
                    coord = _get_single_lon_lat(place)
                    coord_list.append(coord)
                    time.sleep(0.15)
                result.append(coord_list)

            return result

        def format_avoidpolygons(polygon_list: list) -> str:
            # 将 [[[lng, lat], ...], [[lng, lat], ...]] 转换为字符串格式
            polygon_strs = []
            for region in polygon_list:
                if not region or len(region) < 3:
                    continue  # 无效区域跳过
                point_strs = [f"{point[0]:.6f},{point[1]:.6f}" for point in region]
                polygon_strs.append(";".join(point_strs))
            return "|".join(polygon_strs)

        try:
            results = {}
            avoidpolygons_area = format_avoidpolygons(format_address_list(avoidpolygons)) if avoidpolygons else ""
            direction_point = _generate_schema_direction(destination, poi_radius=50000)
            for direction, info in direction_point.items():
                try:
                    origin = info["经纬度信息"]
                    origin_name = info["医院的名字"]

                    response = requests.get(
                        "https://restapi.amap.com/v5/direction/driving",
                        params={
                            "key": gaode_api_key,
                            "origin": origin,
                            "destination": _get_lon_lat(destination),
                            "strategy": 32,
                            "avoidpolygons": avoidpolygons_area
                        }
                    )
                    data = response.json()
                    if data["status"] != "1":
                        results[direction] = f"{origin_name} → {destination}：请求失败（{data.get('info') or data.get('infocode')})"
                        continue

                    paths = data['route']['paths']
                    steps = paths[0]['steps']
                    simplified_steps = []

                    for i, step in enumerate(steps):
                        instruction = step.get('instruction', '')
                        road = step.get('road_name', '')
                        distance = int(step.get('step_distance', 0))
                        if distance < 1000:
                            continue
                        simplified_steps.append(
                            f"第{i + 1}步：{instruction}（{road if road else '（无名道路）'}，约{distance} 米）"
                        )

                    route_text = f"从 {origin_name} 到 {destination} 的路线总长约 {paths[0]['distance']} 米。\n"
                    route_text += "\n".join(simplified_steps)

                    results[direction] = route_text
                    time.sleep(0.2)
                except Exception as e:
                    error_text = f"{origin_name} → {destination}：异常 - {str(e)}"
                    results[direction] = error_text
            return {"results": results}

        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}"}
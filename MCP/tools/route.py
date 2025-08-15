from fastmcp import FastMCP
import os
import requests
import json
import csv
import time
import pandas as pd
from typing import Optional,Dict,Any
from pathlib import Path
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
import logging

# 日志配置
logging.basicConfig(
    level=logging.INFO,  
    format="%(asctime)s - %(levelname)s - %(message)s"
)
df1 = pd.read_excel("poi.xlsx",dtype={"NEW_TYPE": str})
df2 = pd.read_excel("AMap_adcode_citycode.xlsx")

current_dir = Path(__file__).parent
config_path = current_dir / "json_to_csv.json"

with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)
llm = ChatOpenAI(
    openai_api_base="https://api.siliconflow.cn/v1/",
    openai_api_key="sk-yptaszutupiasyfaojsmthassavzgqbrwkncmzcylgkdjyos",    
    model_name="Qwen/Qwen2.5-72B-Instruct",   
)
def register_route_tools(mcp:FastMCP):
    gaode_api_key = os.environ.get('GAODE_API_KEY')
    
    def _get_poi(df, event):
        return str(df.loc[df['小类'] == event, 'NEW_TYPE'].values[0]) 

    def _get_citycode(df, cityname):
        matched = df[df['中文名'].str.contains(cityname, na=False)]
        if matched.empty:
            raise ValueError(f"城市名 '{cityname}' 未在行政编码表中找到")
        return str(matched.iloc[0]['adcode']) 
        
    def _fetch_poi_types(category_list: list, df1, gaode_api_key, location_str, citycode_str, radius=5000, flag=True):
        """
        给定一个 POI 类型列表，依次获取每个类型的 POI 查询结果，并格式化返回 JSON。
        """
        results = {}
        for type_name in category_list:
            try:
                type_code = _get_poi(df1, type_name)
                result = _get_poi_response(gaode_api_key, type_code, location_str, citycode_str, radius, flag)
                time.sleep(0.4)
                if isinstance(result, dict) and result.get("results"):
                    # 直接存到字典里，key 是类型名，value 是查询结果
                    results[type_name] = result
                else:
                    results[type_name] = {"error": "无数据"}
            except Exception as e:
                results[type_name] = {"error": str(e)}
        return results
    
    def _get_poi_response(gaode_api_key, poi_type, location_str, citycode_str, radius, flag):
        try:
            response = requests.get(
                "https://restapi.amap.com/v5/place/around",
                params={
                    "key": gaode_api_key, "types": poi_type, "city_limit": flag,
                    "location": location_str, "radius": radius, "region": citycode_str, 
                    'page_size': 10, 'sortrule': 'distance'
                }
            )
            response.raise_for_status()
            data1 = response.json()

            if data1.get("status") != "1":
                return {"error": f"高德查询失败: {data1.get('info', '')} (code: {data1.get('infocode', '')})"}

            results = [
                {
                    "名称": result.get("name"),
                    "位置": result.get("location"),
                    "地址": result.get("address"),
                    "距离": result.get("distance"),
                }
                for result in data1.get("pois", [])
            ]
            return {"results": results}
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

    def _get_address_info(address: str, city: Optional[str] = None) -> str:
        """
        通过输入一个较为准确的地址信息，然后返回该地址所对应的经纬度信息和地址层级等信息。
        """
        params = {
            'address': address,
            'key': gaode_api_key,
        }
        url = 'https://restapi.amap.com/v3/geocode/geo'
        response = requests.get(url, params=params)

        # 强制使用 UTF-8 解码，防止中文乱码
        response.encoding = 'utf-8'

        data = response.json()
        if data.get('status') == '1':
            results = []
            for geo in data.get("geocodes", []):
                results.append({
                    "country": geo.get("country"),
                    "province": geo.get("province"),
                    "city": geo.get("city"),
                    "citycode": geo.get("citycode"),
                    "district": geo.get("district"),
                    "street": geo.get("street"),
                    "number": geo.get("number"),
                    "adcode": geo.get("adcode"),
                    "level": geo.get("level")
                })
            return json.dumps({"return": results}, ensure_ascii=False)  # 确保输出中文
        else:
            return None


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

    def _peripheral_search(keywords: str, region: str,
        flag: bool = True, radius: int = 5000) -> dict:
        """
        查询指定关键词在指定 POI 类型及区域内的周边信息（例如医疗服务，地形，住宿条件等）。
        
        参数说明：
        - keywords: str：搜索关键词（如“浙江大学”、“人民医院”）
        - origin_poi: str：原始 POI 描述信息，用于提取感兴趣的类型（如“综合医院”、“山脉”等）
        - region: str：所在城市或行政区域名称，用于城市限制搜索
        - flag: bool = True：是否限制在指定城市区域内进行搜索（city_limit 参数）
        - radius: int = 5000：搜索半径（单位：米，默认 5000 米）
        返回值：
        - dict，包括查询结果或错误信息：
            {
                "results": [ {"name": ..., "location": ..., "distance": ..., "address": ...}, ... ]
            }
        """
        geo_types = df1[df1['大类'] == '地名地址信息']['小类'].tolist()
        rescure_types = df1[df1['大类'] == '政府机构及社会团体']['小类'].tolist()
        special_types = df1[df1['大类'] == '公共设施']['小类'].tolist()+df1[df1['大类'] == '事件活动']['小类'].tolist()
        pro_living_types = df1[df1['大类'] == '住宿服务']['小类'].tolist()
        school_types = df1[df1['中类'] == '学校']['小类'].tolist()
        others_types = df1[df1['大类'] == '体育休闲服务']['小类'].tolist()+df1[df1['大类'] == '科教文化服务']['小类'].tolist()

        try:
            location_str = _get_lon_lat(keywords)
            citycode_str = _get_citycode(df2, region) if region else ""
        except Exception as e:
            return {"error": f"位置或区域解析失败: {e}"}
        geo_result = _fetch_poi_types(geo_types, df1, gaode_api_key, location_str, citycode_str, radius, flag)
        rescure_result = _fetch_poi_types(rescure_types, df1, gaode_api_key, location_str, citycode_str, radius, flag)
        special_result = _fetch_poi_types(special_types, df1, gaode_api_key, location_str, citycode_str, radius, flag)
        pro_living_result = _fetch_poi_types(pro_living_types, df1, gaode_api_key, location_str, citycode_str, radius, flag)
        school_result = _fetch_poi_types(school_types, df1, gaode_api_key, location_str, citycode_str, radius, flag)
        others_result = _fetch_poi_types(others_types, df1, gaode_api_key, location_str, citycode_str, radius, flag)

        all_results = {}
        for block in [geo_result, rescure_result, special_result, pro_living_result, school_result, others_result]:
            if isinstance(block, dict):
                all_results.update(block)

        merged_rows = []
        for category, content in all_results.items():
            if isinstance(content, dict) and "results" in content and isinstance(content["results"], list):
                for item in content["results"]:
                    if isinstance(item, dict) and item:  # 跳过空项
                        row = {"类别": category, **item}
                        merged_rows.append(row)
        if merged_rows:
            fieldnames = set()
            for r in merged_rows:
                fieldnames.update(r.keys())
            fieldnames = ["类别"] + sorted([k for k in fieldnames if k != "类别"])

            csv_filename =current_dir / "results2.csv"
            with open(csv_filename, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(merged_rows)

            logging.info(f"已将合并后的数据转换为 CSV 文件：{csv_filename}")
        else:
            logging.warning("没有可写入的数据。")
        prompt = ChatPromptTemplate.from_template("""
        你是数据去重助手。输入是一组“名称 | 距离km”。你的任务是找出需要被删除的名称（重复项中的次要项）。
        严格遵守以下规则——任何违反都会被判错：

        【核心判定】
        A. 只有当某名称存在至少一个【近似同名伙伴】且两者距离 ≤ 2 公里时，该名称才可能需要删除；否则一律保留。并且并不是名字很像的两个地名就一定是同一实体，，可以借助距离信息加以判定，并必须符合B和C的定义。
        B. 【近似同名伙伴】定义：去除空格/全半角差异/标点后，两名称在语义上指向同一机构，只是附加了楼栋、学院、馆、中心、部、科、店内空间（如大堂/前台/停车场）等下位描述。
        C. 以下属于不同实体，即使距离 ≤ 2 公里也不得合并/删除：不同校区/园区/园（如“东/西/南/北/XX校区/园区/园/分校/分院/分院区/国际校区/XX园区/基地/校门”），不同品牌门店（XX店/XX门店），不同社区/小区/村/街道。

        【保留谁、删谁】
        D. 在一个近似同名组内，仅保留最具基名的那条：优先保留无楼栋/学院/大堂等后缀的纯机构名；若有多个候选，保留名称更短、更通用的那条。
        E. 组内其余项（带学院/图书馆/大堂/中心/馆/楼/教学楼/食堂/停车场/服务台/分部/培训中心/研究院/附属XX/XX实验室等）标记为需删除。
        F. 你只能输出确实出现在输入中的名称，禁止臆造或改写。

        【硬性限制，避免误删】
        G. 若某名称在全体列表中没有任何近似同名伙伴（按B定义），必须保留，不得输出（例如杭州市文海凌云小学若无近似项就不能出现于输出）。
        H. 若仅为名称前缀相同但实体语义不同（如XX大学、XX大学东校区/XX大学国际校区），视为不同实体，不得输出删除。
        I. 若最终没有任何需要删除的名称，输出空列表 []。

        【输出格式要求】
        - 直接输出一个合法的 Python 列表（JSON 数组格式），每个元素是一个字符串，对应需删除的名称。
        - 不要输出多余的文字、注释、换行、说明。
        - 确保输出可以被 Python `json.loads` 正确解析。

        下面是数据（名称 | 距离km）：
        {records}
        """)
        csv_path = (current_dir / "results2.csv").resolve()
        logging.info("准备读取：%s", csv_path)

        if not csv_path.exists():
            logging.error("文件不存在：%s（注意脚本的 current_dir 是否正确）", csv_path)
            raise FileNotFoundError(csv_path)
        data = pd.read_csv(csv_path)
        parser = JsonOutputParser()
        records = "\n".join(f"{row['名称']} | {row['距离']}" for _, row in data.iterrows())
        chain = prompt | llm | parser
        delete_list = chain.invoke({"records": records})
        data = data[~data["名称"].isin(delete_list)].reset_index(drop=True)
        data = data.drop(columns=["位置"])
        result = data.to_dict(orient="records")
        return {'summary': result}
   
    @mcp.tool()
    def route_summary(address: str, city_name: str) -> str:
        """
        根据输入的地址信息和城市名，获取：
        1. 该地址的经纬度坐标；
        2. 该地址的详细行政区划信息；
        3. 该地址周边的重点设施与地理要素信息。

        参数:
            address (str): 详细地址，例如“杭州市滨江区江南大道588号”
            city_name (str): 城市名称，例如“杭州”

        返回:
            格式化字符串，包含经纬度、地址解析结果和周边POI信息
        """
        try:
            location = _get_lon_lat(address)
            address_info = _get_address_info(address)
            around_info = _peripheral_search(address, city_name)

            result = (
                f"📍 **事发地经纬度位置**：{location}\n\n"
                f"🏙️ **详细地址信息**：{address_info}\n\n"
                f"📌 **周边重点设施信息**：\n{around_info}"
            )
            return result

        except Exception as e:
            return f"获取路线摘要失败：{str(e)}"
from fastmcp import FastMCP
import os
import requests
import time
from dotenv import set_key

def register_weather_tools(mcp:FastMCP):
    API_host = os.environ.get('HEFEN_API_HOST')
    API_KEY = os.environ.get('HEFEN_API_KEY')  

    def _get_city_id(city_name:str) -> str:
        """根据中文城市名获取和风天气 location ID"""
        headers = {'X-QW-Api-Key': f'{API_KEY}'}
        params = {'location': city_name}
        url = f'https://{API_host}/geo/v2/city/lookup'
        response = requests.get(url, headers=headers,params=params)
        # 检查返回是否为 401 Unauthorized，若是则重新生成 JWT
        if response.status_code == 401:
            return f"获取天气信息失败，错误响应为：{response.status_code}"
        data = response.json()

        if data.get('code') == '200':
            return data['location'][0]['id']
        else:
            raise ValueError(f"找不到城市: {city_name}，错误信息: {data}")
    def _get_lon_lat(city_name: str) -> str:
        """根据中文城市名获取城市经纬度信息"""
        headers = {'X-QW-Api-Key': f'{API_KEY}'}
        params = {'location': city_name}
        url = f'https://{API_host}/geo/v2/city/lookup'
        response = requests.get(url, headers=headers,params=params)
        if response.status_code == 401:
            return f"获取经纬度信息失败，错误响应为：{response.status_code}"
        data = response.json()

        if data.get('code') == '200':
            return (round(float(data['location'][0]['lat']), 2), round(float(data['location'][0]['lon']), 2))
        else:
            raise ValueError(f"找不到城市: {city_name}，错误信息: {data}")        
    def _get_weather(city_name: str) -> str:
        """
        根据和风天气 location ID 获取天气信息，并且自动的去调用获取天气预警的函数，并判断是否存在天气预警。
        其中返回的信息包含：当前天气（中文描述、温度、湿度、风速，体感温度，对于当前天气的一个描述），
        如果有预警，返回预警的详细信息。
        """
        location_id = _get_city_id(city_name)
        headers = {'X-QW-Api-Key': f'{API_KEY}'}
        params = {'location': location_id}
        url = f'https://{API_host}/v7/weather/now'
        response = requests.get(url, headers=headers, params=params)

        #print(f"Response Status Code: {response.status_code}")
        #print(f"Response Text: {response.text}")

        try:
            data = response.json()  
        except ValueError as e:
            return f"Error: {e}"
        
        if data.get("code") != '200':
            raise Exception(f'获取天气信息失败: {data}, 错误响应为：{response.status_code}')

        # 获取天气信息
        weather = data['now']
        weather_info = (
            f"🌍 城市: {city_name}\n"
            f"🌤 天气: {weather['text']}\n"
            f"🌡 温度: {weather['temp']}°C\n"
            f"💧 湿度: {weather['humidity']}%\n"
            f"🌬 风速: {weather['windSpeed']} m/s\n"
            f"体感温度: {weather['feelsLike']}°C\n"
        )
        # 获取天气预警信息
        warning_info = _get_weather_warning(city_name)
        # 如果有天气预警，将预警信息添加到天气信息中
        if warning_info != "当前没有天气预警。":
            weather_info += f"\n⚠️ 天气预警信息:\n{warning_info}"
        else:
            weather_info += f"\n{warning_info}"
        return weather_info

    def _get_weather_warning(city_name: str) -> str:
        """
        根据当前城市名获取当天该城市是否存在天气预警情况，并返回详细的预警信息。
        如果该城市存在天气预警，返回包含以下内容的字符串：
        - 预警严重等级
        - 预警类型名称
        - 预警的紧迫程度 (可能为空)
        - 预警的确定性 (可能为空)
        - 预警详细文字描述 
        - 预警的开始时间与结束时间
        如果不存在天气预警，则返回提示信息：“当前没有天气预警”。
        参数:
        - city_name: 需要查询的城市名称（字符串）
        返回:
        - 如果存在天气预警，返回预警的详细信息字符串；如果没有，返回提示“当前没有天气预警”。
        """
        location_id = _get_city_id(city_name)
        headers = {'X-QW-Api-Key': f'{API_KEY}'}
        params = {'location': location_id}
        url = f'https://{API_host}/v7/warning/now'
        
        response = requests.get(url, headers=headers, params=params)
        # 检查响应是否为有效的 JSON
        try:
            data = response.json()  
        except ValueError as e:
            #print(f"Error parsing JSON: {e}")
            return f"Error: {e}"
        if data.get("code") != '200':
            raise Exception(f'获取天气信息失败: {data}, 错误响应为：{response.status_code}')
        if data['warning']:
            warning = data['warning'][0]
        else:
            return "当前没有天气预警。"
        warning_info = (
            f"城市: {city_name}\n"
            f"预警类型: {warning['typeName']}\n"
            f"严重等级: {warning['severity']}\n"
            f"严重等级颜色: {warning.get('severityColor', '无')}\n"
            f"紧迫程度: {warning.get('urgency', '无')}\n"
            f"确定性: {warning.get('certainty', '无')}\n"
            f"详细描述: {warning['text']}\n"
            f"预警开始时间: {warning['startTime']}\n"
            f"预警结束时间: {warning['endTime']}\n"
        )
        return warning_info
    def _get_forecast3d(city_name: str) -> str:
        """
        根据指定城市的信息返回该城市未来3天的天气预报情况
        返回格式化的字符串，包含每天的关键天气信息
        """
        # 假设这些函数/变量已在其他地方定义
        location_id = _get_city_id(city_name)
        # headers = {'Authorization': f'Bearer {Jwt_token}'}
        headers = {'X-QW-Api-Key': f'{API_KEY}'}
        params = {'location': location_id}
        url = f'https://{API_host}/v7/weather/3d'
        
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()  
            data = response.json()
            if data.get("code") != '200':
                raise Exception(f'获取天气信息失败: {data}, 状态码：{response.status_code}')
            daily_forecasts = data.get("daily", [])
            if not daily_forecasts:
                return "未找到天气预报数据"
            # 格式化每天的天气信息
            formatted_forecasts = []
            for day in daily_forecasts[:3]:  
                forecast_str = (
                    f"📅 日期: {day.get('fxDate', 'N/A')}\n"
                    f"🌡️ 温度: {day.get('tempMin', 'N/A')}℃ ~ {day.get('tempMax', 'N/A')}℃\n"
                    f"☀️ 白天: {day.get('textDay', 'N/A')} | 💨 风力: {day.get('windScaleDay', 'N/A')}级 ({day.get('windSpeedDay', 'N/A')}km/h)\n"
                    f"🌙 夜间: {day.get('textNight', 'N/A')} | 💨 风力: {day.get('windScaleNight', 'N/A')}级 ({day.get('windSpeedNight', 'N/A')}km/h)\n"
                    f"💧 湿度: {day.get('humidity', 'N/A')}% | 🌧️ 降水: {day.get('precip', '0.0')}mm\n"
                    f"☂️ 紫外线: {day.get('uvIndex', 'N/A')}"
                )
                formatted_forecasts.append(forecast_str)
            separator = "\n" + "━" * 40 + "\n"
            return separator.join(formatted_forecasts)
        
        except requests.exceptions.RequestException as e:
            return f"网络请求错误: {str(e)}"
        except ValueError as e:
            return f"JSON解析错误: {str(e)}"
        except Exception as e:
            return f"处理错误: {str(e)}"
    def _get_air_quality(city_name: str) -> str:
        """
        根据城市名获取该城市当前空气质量信息。
        
        返回字段包括：
        - AQI 值
        - 首要污染物
        - 健康影响描述
        - 健康建议（对普通人群）
        - 空气质量颜色（RGBA）
        """
        headers = {'X-QW-Api-Key': f'{API_KEY}'}
        latitude, longitude = _get_lon_lat(city_name)
        
        url = f'https://{API_host}/airquality/v1/current/{latitude}/{longitude}'
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return f"获取空气质量信息失败，HTTP状态码：{response.status_code}"

        data = response.json()

        index_data = data.get("indexes", [{}])[0]

        aqi = index_data.get("aqi", "未知")
        pollutant = index_data.get("primaryPollutant", "无污染")
        category = index_data.get("category", "未知")
        health_effect = index_data.get("health", {}).get("effect", "无描述")
        advice = index_data.get("health", {}).get("advice", {}).get("generalPopulation", "无建议")

        color_data = index_data.get("color", {})
        red = color_data.get("red", 0)
        green = color_data.get("green", 0)
        blue = color_data.get("blue", 0)
        alpha = color_data.get("alpha", 1)
        hex_color = "#{:02X}{:02X}{:02X}".format(red, green, blue)
        result = (
            f"🌍 城市：{city_name}\n"
            f"📈 空气质量指数（AQI）：{aqi}\n"
            f"🔍 空气质量等级：{category}\n"
            f"🦠 首要污染物：{pollutant}\n"
            f"💡 健康影响：{health_effect}\n"
            f"👥 健康建议（普通人群）：{advice}\n"
            f"🎨 指数颜色：{hex_color}"
        )

        return result   
    @mcp.tool()
    def weather_summary(city_name: str, judge_flag: bool = False) -> str:
        """
        获取指定城市的天气摘要信息。
        当用户询问某地的天气情况时（如“北京天气如何？”），调用此函数。
        它会自动获取该城市的当前实况、是否存在天气预警，以及未来三天的天气预报，并将它们整理成一段结构化文本返回。
        当用户输入当前的灾害类型为火灾时，则需要而外在考虑空气质量指数对于救援行为的影响，此时将judge_flag设置为True。
        输入:
            city_name (str): 城市中文名，例如 "杭州"、"北京"
        输出:
            一段纯文本格式的天气摘要，包含：
            - 当前天气现象、气温、体感温度、湿度、风速
            - 当前是否有天气预警（若有，则返回预警详情）
            - 未来三天的白天/夜间天气、温度、风力、湿度、降水、紫外线等数据
        """
        try:
            now = _get_weather(city_name)
            warn = _get_weather_warning(city_name)
            forecast = _get_forecast3d(city_name)
            if judge_flag:
                air = _get_air_quality(city_name)
            else:
                air = "当前自然灾害受空气质量影响较小"

            return (
            f"【当前天气实况】\n{now}\n\n"
            f"【天气预警信息】\n{warn}\n\n"
            f"【未来三天天气预报】\n{forecast}"
            f"【空气质量指数】\n{air}"
            )

        except Exception as e:
            return f"获取天气信息失败：{str(e)}"
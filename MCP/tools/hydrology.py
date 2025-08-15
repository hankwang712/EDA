from fastmcp import FastMCP
import os
import requests
from datetime import datetime, timedelta
import logging

logger = logging.getLogger("Hydrology_Tools")

def register_hydrology_tools(mcp: FastMCP):
    USGS_API_URL = os.environ.get('USGS_API_URL')
    
    def _get_hydrology_data(station_id: str, parameter: str = '00060') -> dict:
        """
        获取指定水文站的水文数据
        参数:
            station_id: 水文站ID
            parameter: 水文参数代码 (00060=流量, 00065=水位)
        返回:
            包含水文数据的字典
        """
        params = {
            'format': 'json',
            'sites': station_id,
            'parameterCd': parameter,
            'siteStatus': 'all'
        }
        
        try:
            response = requests.get(USGS_API_URL, params=params)
            response.raise_for_status()
            data = response.json()
            
            if not data['value']['timeSeries']:
                return {"error": f"找不到水文站 {station_id} 的数据"}
                
            values = data['value']['timeSeries'][0]['values'][0]['value']
            latest_value = values[0] if values else None
            
            if not latest_value:
                return {"error": f"水文站 {station_id} 无可用数据"}
                
            station_info = data['value']['timeSeries'][0]['sourceInfo']
            variable_info = data['value']['timeSeries'][0]['variable']
            
            return {
                "station_id": station_id,
                "station_name": station_info['siteName'],
                "parameter": variable_info['variableName'],
                "unit": variable_info['unit']['unitCode'],
                "value": latest_value['value'],
                "timestamp": latest_value['dateTime'],
                "latitude": station_info['geoLocation']['geogLocation']['latitude'],
                "longitude": station_info['geoLocation']['geogLocation']['longitude']
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API请求失败: {str(e)}")
            return {"error": f"API请求失败: {str(e)}"}
        except (KeyError, IndexError) as e:
            logger.error(f"数据解析错误: {str(e)}")
            return {"error": f"数据解析错误: {str(e)}"}

    @mcp.tool()
    def hydrology_summary(station_id: str) -> str:
        """
        获取指定水文站的综合水文信息摘要
        """
        key_parameters = {
            '00065': ('实时水位', '🌊', '水位'),
            '62616': ('水位日涨幅', '📈', '水位'),
            '00060': ('瞬时流量', '💧', '流量'),
            '00062': ('24小时流量涨幅', '🚀', '流量'),
            '00045': ('小时降雨量', '🌧️', '降雨'),
            '00046': ('日降雨量', '📅', '降雨'),
            '80155': ('水体含沙量', '🏖️', '泥沙'),
            '62611': ('地下水位埋深', '⛰️', '地下水'),
            '62625': ('表层土壤含水率', '💦', '土壤'),
            '62619': ('超警戒水位持续时长', '⚠️', '综合')
        }
        
        results = {}
        for param_code, (param_name, emoji, category) in key_parameters.items():
            data = _get_hydrology_data(station_id, param_code)
            if 'error' not in data:
                results[param_code] = {
                    'name': param_name,
                    'emoji': emoji,
                    'value': data['value'],
                    'unit': data['unit']
                }
            else:
                results[param_code] = {
                    'name': param_name,
                    'emoji': emoji,
                    'value': '数据不可用',
                    'unit': ''
                }
        
        base_data = _get_hydrology_data(station_id, '00060')
        station_name = base_data.get('station_name', '未知站点') if 'error' not in base_data else '未知站点'
        timestamp = base_data.get('timestamp', '未知时间') if 'error' not in base_data else '未知时间'
        
        summary = (
            f"🌍 水文站: {station_name} ({station_id})\n"
            f"🕒 最后更新时间: {timestamp}\n"
            "="*40 + "\n"
            "📊 核心水文监测指标（灾害风险评估）:\n"
        )
        
        categories = {
            '水位': "\n🌊 水位指标 (洪水预警):",
            '流量': "\n💧 流量指标 (洪水风险评估):",
            '降雨': "\n🌧️ 降雨指标 (洪水触发因素):",
            '泥沙': "\n🏖️ 泥沙指标 (次生灾害评估):",
            '地下水': "\n⛰️ 地下水指标 (干旱监测):",
            '土壤': "\n💦 土壤指标 (滑坡风险):",
            '综合': "\n⚠️ 综合指标 (灾害持续时间):"
        }
        
        organized = {cat: [] for cat in categories}
        for param_code, info in key_parameters.items():
            _, _, category = info
            organized[category].append((param_code, results[param_code]))
        
        for cat, header in categories.items():
            if organized[cat]:
                summary += header + "\n"
                for code, data in organized[cat]:
                    summary += f"  {data['emoji']} {data['name']}: {data['value']} {data['unit']}\n"
        
        unavailable_count = sum(1 for data in results.values() if data['value'] == '数据不可用')
        if unavailable_count > 0:
            summary += f"\n注: {unavailable_count}个指标数据不可用，可能该站点未监测这些参数"
        
        return summary

    @mcp.tool()
    def get_hydrology_trend(station_id: str, days: int = 7) -> str:
        """
        获取水文站的历史趋势数据
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        params = {
            'format': 'json',
            'sites': station_id,
            'parameterCd': '00060',
            'startDT': start_date.strftime('%Y-%m-%d'),
            'endDT': end_date.strftime('%Y-%m-%d')
        }
        
        try:
            response = requests.get(USGS_API_URL, params=params)
            response.raise_for_status()
            data = response.json()
            
            if not data['value']['timeSeries']:
                return f"找不到水文站 {station_id} 的历史数据"
                
            values = data['value']['timeSeries'][0]['values'][0]['value']
            if not values:
                return f"水文站 {station_id} 无历史数据"
                
            first_value = float(values[0]['value'])
            last_value = float(values[-1]['value'])
            trend = "上升" if last_value > first_value else "下降"
            change_percent = abs((last_value - first_value) / first_value) * 100
            
            return (
                f"📈 {station_id} 水文站 {days} 天流量趋势:\n"
                f"• 起始流量: {first_value:.2f} ft³/s\n"
                f"• 当前流量: {last_value:.2f} ft³/s\n"
                f"• 变化趋势: {trend} ({change_percent:.1f}%)\n"
                f"• 数据点数量: {len(values)}"
            )
            
        except requests.exceptions.RequestException as e:
            return f"获取历史数据失败: {str(e)}"
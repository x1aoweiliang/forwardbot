import time

import requests

url = "https://api.vidu.com/ent/v2/reference2video"
url = "https://api.vidu.com/ent/v2/text2video"
api_key = "vda_944513216795385856_wWcSwnlIG0VkImQF4vTItj7PZ8ipcKrY"
headers = {
    "Authorization": f"Token {api_key}",
    "Content-Type": "application/json",
  }
prompt = """
Cinematic medium shot, 10-second slow dolly-in.** A naked Chinese man and a naked Chinese woman in a deep, heavy, and slow-paced intimate embrace, moving rhythmically together. The 
camera slowly moves closer (Dolly-in) to focus on their grinding, skin-on-skin friction and the way their bodies press together. Dim, moody low-key lighting with amber highlights, shadows 
masking parts of their bodies to create mystery. The woman, eyes half-closed in pleasure, clings to his neck and whispers breathily: **“不要停... 就是这里...” (Don't stop... right 
there...)**. Hyper-realistic, 8k, cinematic shadow, heavy breathing, intimate atmosphere, detailed skin texture, professional film lighting.
"""
# prompt_url = "https://api.vidu.com/ent/v2/prompt-enhance"
# response = requests.request("POST", prompt_url, headers=headers, json={"prompt": prompt})
# print(response.json())
# prompt = response.json()["result"]

body = {
    "model": "viduq3-ns",
    "prompt": prompt,
    "duration": 10,
    "aspect_ratio": "16:9",
    "resolution": "720p",
    "movement_amplitude": "auto",
    "moderation": "disabled",
    "off_peak": True,
    # "subjects": [
    #       {
    #         "name": "mei",
    #         "images": [
    #           "https://jl-ai-drama.tos-cn-beijing.volces.com/ai-drama/original/upload/2026/04/23/赵玲玲浴巾装_20260423202625A443.png",
    #         ]
    #       },
    #       {
    #         "name": "jack",
    #         "images": [
    #           "https://jl-ai-drama.tos-cn-beijing.volces.com/ai-drama/original/upload/2026/04/30/光头.png",
    #         ]
    #       }
    #     ],
  }

response = requests.request("POST", url, headers=headers, json=body,)
print(response.json())

headers = {
    "Authorization": f"Token {api_key}"
  }
body = None
task_id = response.json()["task_id"]
query_task_url = f"https://api.vidu.com/ent/v2/tasks/{task_id}/creations"
start_time = time.time()
while(True):
    response = requests.request("GET", query_task_url, headers=headers, json=body)
    if response.json()["state"] == "success":
        print("生成的视频URL:", response.json()["creations"][0]["url"])
        print("生成的视频封面URL:", response.json()["creations"][0]["cover_url"])
        break
    print(response.json())
    time.sleep(2)
end_time = time.time()
print(f"视频生成耗时: {end_time - start_time} 秒")

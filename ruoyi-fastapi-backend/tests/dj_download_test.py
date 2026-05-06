import requests

url = "https://svapi.shimiduanju.com/mobile/chapter/get-video-url"

payload = {
  'chapterId': "285",
  'appid': "wx90181be9ca158e85"
}

headers = {
  'User-Agent': "Mozilla/5.0 (iPhone; CPU iPhone OS 26_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.70(0x18004632) NetType/WIFI Language/zh_CN miniProgram/wx90181be9ca158e85",
  'devicetype': "2",
  'userid': "1159770",
  'sec-fetch-site': "same-site",
  'priority': "u=3, i",
  'vplatform': "0",
  'accept-language': "zh-CN,zh-Hans;q=0.9",
  'sec-fetch-mode': "cors",
  'token': "b9c6ea280942b13179f250e0f653aea7",
  'origin': "https://svplay.shimiduanju.com",
  'referer': "https://svplay.shimiduanju.com/",
  'sec-fetch-dest': "empty"
}

response = requests.post(url, data=payload, headers=headers)

print(response.text)
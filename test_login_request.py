import urllib.request
import json

def test_login():
    url = "http://127.0.0.1:8001/login"
    data = json.dumps({
        "email": "savvytechno.dev@gmail.com",
        "password": "password"
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as response:
            print(f"Status: {response.getcode()}")
            print(f"Response: {response.read().decode('utf-8')}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code}")
        print(f"Response: {e.read().decode('utf-8')}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_login()

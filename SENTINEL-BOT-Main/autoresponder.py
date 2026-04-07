import json
import os
import re

AUTORESPONDER_FILE = "autoresponder.json"

def load_responses():
    if os.path.exists(AUTORESPONDER_FILE):
        with open(AUTORESPONDER_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {"responses": []}
    return {"responses": []}

def save_responses(data):
    with open(AUTORESPONDER_FILE, "w") as f:
        json.dump(data, f, indent=4)

def add_response(trigger, response, match_type="contains"):
    data = load_responses()
    for r in data["responses"]:
        if r["trigger"].lower() == trigger.lower():
            r["response"] = response
            r["match"] = match_type
            save_responses(data)
            return False
    data["responses"].append({
        "trigger": trigger.lower(),
        "response": response,
        "match": match_type
    })
    save_responses(data)
    return True

def remove_response(trigger):
    data = load_responses()
    before = len(data["responses"])
    data["responses"] = [r for r in data["responses"] if r["trigger"].lower() != trigger.lower()]
    save_responses(data)
    return len(data["responses"]) < before

def get_all_responses():
    return load_responses().get("responses", [])

async def check_message(message):
    data = load_responses()
    content = message.content.lower().strip()
    for rule in data.get("responses", []):
        trigger = rule["trigger"].lower()
        match_type = rule.get("match", "contains")
        matched = False
        if match_type == "exact":
            matched = content == trigger
        elif match_type == "startswith":
            matched = content.startswith(trigger)
        elif match_type == "regex":
            try:
                matched = bool(re.search(trigger, content))
            except Exception:
                pass
        else:
            matched = trigger in content
        if matched:
            response = rule["response"].replace("{user}", message.author.mention).replace("{username}", message.author.display_name)
            await message.channel.send(response)
            return True
    return False

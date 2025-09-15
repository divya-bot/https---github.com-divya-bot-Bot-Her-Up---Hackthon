from flask import Flask, render_template, request, session
import PyPDF2
from openai import AzureOpenAI
import requests
from requests.auth import HTTPBasicAuth
import os
from dotenv import load_dotenv
import re
import pandas as pd
from flask import send_file
import io

load_dotenv()

app = Flask(__name__)

dotenv_path = r"C:\Users\divya.bajaj_jadeglob\.vscode\flask_webpage\config.env"
load_dotenv(dotenv_path=dotenv_path)
app.secret_key = "supersecretkey"  # Needed for session storage

# ---------------- Azure OpenAI ----------------
endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
subscription_key = os.getenv("AZURE_OPENAI_API_KEY")
deployment = "hackathon-group3"
api_version = "2024-02-01"

client = AzureOpenAI(
    api_key=subscription_key,
    api_version=api_version,
    azure_endpoint=endpoint,
)

# ---------------- Jira Config ----------------
jira_server = os.getenv("JIRA_SERVER")
jira_email = os.getenv("JIRA_EMAIL")
jira_token = os.getenv("JIRA_API_TOKEN")
jira_project = "ATS"

# ---------------- Functions ----------------
def extract_text_from_pdf(pdf_file):
    """Extract text from uploaded PDF"""
    reader = PyPDF2.PdfReader(pdf_file)
    return "\n".join(page.extract_text() for page in reader.pages if page.extract_text())

def export_to_excel(stories):
    """Export user stories/test cases to Excel and return as a download"""
    if not stories:
        return None

    df = pd.DataFrame(stories)

    if "title" in df.columns:
        df.rename(columns={"title": "Stories"}, inplace=True)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Stories", index=False)

    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="TestCases.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

def split_stories(output: str):
    if not output:
        return []

    # Match each story block starting with "1) User Story:", "2) User Story:", etc.
    pattern = re.compile(
    r'(?:\d+\)\s*)(.*?)(?=\n\d+\)|\Z)',
    re.DOTALL
)
    matches = pattern.findall(output)

    stories = []
    for m in matches:
        block = m.strip()

        # Title 
        parts = block.split("\n", 1)
        title = parts[0].replace("User Story:", "").replace("Story:", "").replace("User Story -", "").replace("Story -", "").strip()

        # Acceptance Criteria (if present)
        criteria_match = re.search(r'Acceptance Criteria.*?:([\s\S]*)', block, re.IGNORECASE)
        criteria = criteria_match.group(1).strip() if criteria_match else ""
    
        stories.append({
            "title": title[:150],
            "description": criteria
        })

    return stories


def create_jira_story(summary, description):
    """Call Jira REST API to create a Story"""
    url = f"{jira_server}/rest/api/3/issue"
    auth = HTTPBasicAuth(jira_email, jira_token)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    payload = {
  "fields": {
    "project": {
      "key": "ATS"
    },
    "summary": summary,
    "issuetype": {
      "name": "Story"
    },
    "reporter": {
      "id": "6351307e76b91b62562c4f2d"
    },
    "description": {
      "type": "doc",
      "version": 1,
      "content": [
        {
          "type": "paragraph",
          "content": [
            { "type": "text", "text": description}
          ]
        }
      ]
    }
  }
}

    response = requests.post(url, headers=headers, auth=auth, json=payload)

    if response.status_code == 201:
        return response.json().get("key")
    else:
        return f"Error {response.status_code}: {response.text}"

# ---------------- API Endpoints ----------------
@app.route("/", methods=["GET", "POST"])
def index():
    output = None
    error_message = None
    error_message_jira = None
    stories = session.get("stories", [])
    issues = []

    if request.method == "POST":
        action = request.form.get("action")
        user_prompt = request.form.get("prompt", "").strip()
        uploaded_file = request.files.get("file")

        if action == "generate":
            if not user_prompt or not uploaded_file:
                error_message = "Please provide both a prompt and a BRD document."
            else:
                extracted_text = ""
                if uploaded_file.filename.endswith(".pdf"):
                    extracted_text = extract_text_from_pdf(uploaded_file)
                elif uploaded_file.filename.endswith(".txt"):
                    extracted_text = uploaded_file.read().decode("utf-8")
                else:
                    error_message = "Only PDF and TXT files are supported."

                if not error_message:
                    final_prompt = f"""
                    BRD Content:
                    {extracted_text}

                    User Instruction:
                    {user_prompt}

                    Your job is to only extract information relevant to this instruction.
            If prompt contains user stories, the format should be like 1) User Story: story description like User login with email or mobile + password for each requirement
            If prompt contains test cases, the format should be Like 1) TC001 : Test case description for each requirement
                    """

                    try:
                        response = client.chat.completions.create(
                            model=deployment,
                            messages=[
                                {"role": "system", "content": "You are a strict assistant. Only respond with details directly relevant to the user's instruction."},
                                {"role": "user", "content": final_prompt},
                            ],
                            max_completion_tokens=2500
                        )
                        output = response.choices[0].message.content
                        stories = split_stories(output)
                        session["stories"] = stories
                    except Exception as e:
                        error_message = f"Error: {e}"

        elif action == "create_jira":
            titles = request.form.getlist("titles")
            descriptions = request.form.getlist("descriptions")
            print("Titles received:", titles)
            print("Descriptions received:", descriptions)

            if not titles or not descriptions:
                error_message_jira = "No stories available. Please generate stories first."
            else:
                for title, desc in zip(titles, descriptions):
                    try:
                        issue_key = create_jira_story(title, desc)
                        issues.append(issue_key)
                    except Exception as e:
                        issues.append(f"Error creating story '{title}': {e}")
        elif action == "export_to_excel":
            if stories:
                return export_to_excel(stories)   
            else:
                error_message = "No stories available. Please generate stories first."

    return render_template(
        "index.html",
        output=output,
        error_message=error_message,
        error_message_jira=error_message_jira,   
        stories=stories,
        issues=issues,
        jira_server=jira_server
    )

# ---------------- Run App ----------------
if __name__ == "__main__":
    app.run(port=8000, debug=True)
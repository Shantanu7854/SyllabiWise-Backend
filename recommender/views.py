from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import yt_dlp
import google.generativeai as genai
import os
import ast
import re

from dotenv import load_dotenv
from .mongo import collection
from django_ratelimit.decorators import ratelimit
from django.utils.decorators import method_decorator
from django.http import JsonResponse

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# 🔐 Handle rate-limit violation
def rate_limited(request, exception=None):
    return JsonResponse({
        "error": "Rate limit exceeded. Please try again later."
    }, status=429)

# 🧹 Clean syllabus input into topic list
def extract_syllabus_topics(raw_syllabus: str) -> list[str]:
    lines = raw_syllabus.split('\n')
    topics = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Remove bullets, numbering, modules, units
        line = re.sub(r'^(\d+\.|\-|\*|Module \d+|Unit \d+|Module \d+:|Unit \d+:)\s*', '', line, flags=re.IGNORECASE)

        # Remove things like "6L" or "8L" at the end
        line = re.sub(r'\d+\s*[L|l]$', '', line).strip()

        # Skip short meaningless lines
        if len(line.split()) < 2:
            continue

        topics.append(line)

    return topics

@method_decorator(ratelimit(key='ip', rate='5/h', method='POST', block=True), name='post')
class PlaylistAnalyzeView(APIView):
    def post(self, request):
        playlist_url = request.data.get("playlist_url")
        syllabus = request.data.get("syllabus")

        if not playlist_url or not syllabus:
            return Response(
                {"error": "playlist_url and syllabus are required."},
                status=400
            )

        # Step 1: Extract video titles from YouTube playlist
        try:
            ydl_opts = {
                'quiet': True,
                'extract_flat': True,
                'skip_download': True
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                playlist_dict = ydl.extract_info(playlist_url, download=False)
                video_titles = [entry['title'] for entry in playlist_dict.get('entries', [])]
        except Exception as e:
            return Response(
                {"error": f"Error extracting playlist: {str(e)}"},
                status=500
            )

        # Step 2: Clean syllabus into topic list
        syllabus_topics = extract_syllabus_topics(syllabus)

        # Step 3: Prepare prompt for Gemini
        prompt = (
            "You are an AI assistant. Match YouTube video titles with syllabus topics only.\n"
            "Ignore long descriptions, codes, or line breaks.\n"
            "Return ONLY a Python-style list of dictionaries with 'topic' and 'videos'.\n\n"
            "Example:\n[{'topic': 'Binary Trees', 'videos': ['Binary Search Tree', 'AVL Tree']}]\n\n"
            "Syllabus Topics:\n" +
            "\n".join(f"- {topic}" for topic in syllabus_topics) + "\n\n" +
            "Video Titles:\n" +
            "\n".join([f"{i+1}. {title}" for i, title in enumerate(video_titles)])
        )

        # Step 4: Call Gemini API
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)

            raw_output = response.text
            print("📥 Gemini raw output:\n", raw_output)

            # Clean Markdown ```python\n...\n``` block if it exists
            code_block_pattern = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)
            match = code_block_pattern.search(raw_output)
            clean_output = match.group(1) if match else raw_output

            # Parse the clean Python-style list
            parsed_response = ast.literal_eval(clean_output.strip())

        except Exception as e:
            raw_output = locals().get("raw_output", "No response generated.")
            return Response({
                "error": "Gemini API failed or returned invalid output.",
                "details": str(e),
                "raw_output": raw_output
            }, status=500)

        # Step 5: Save result to MongoDB
        try:
            collection.insert_one({
                "playlist_url": playlist_url,
                "syllabus": syllabus,
                "video_titles": video_titles,
                "recommendations": parsed_response
            })
        except Exception as e:
            return Response(
                {"error": f"MongoDB save failed: {str(e)}"},
                status=500
            )

        # Step 6: Return response
        return Response({"recommendations": parsed_response}, status=200)

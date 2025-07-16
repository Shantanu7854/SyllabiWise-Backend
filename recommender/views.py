from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django_ratelimit.decorators import ratelimit
from django.utils.decorators import method_decorator
from django.http import JsonResponse

import yt_dlp
import google.generativeai as genai
import os
import ast
import re
import json
import html

from dotenv import load_dotenv
from .mongo import collection

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# 🔐 User Registration
class RegisterView(APIView):
    def post(self, request):
        username = request.data.get('username')
        email = request.data.get('email')
        password = request.data.get('password')

        if not username or not password:
            return Response({"error": "Username and password are required."}, status=400)

        if User.objects.filter(username=username).exists():
            return Response({"error": "Username already exists."}, status=400)

        user = User.objects.create(
            username=username,
            email=email,
            password=make_password(password)
        )
        return Response({"message": "User registered successfully."}, status=201)

# 🛡 Rate-limit fallback
def rate_limited(request, exception=None):
    return JsonResponse({
        "error": "Rate limit exceeded. Please try again later."
    }, status=429)

# 📘 Syllabus cleaner
def extract_syllabus_topics(raw_syllabus: str) -> list[str]:
    lines = raw_syllabus.split('\n')
    topics = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Remove bullets, numbering, modules, etc.
        line = re.sub(r'^(\d+\.|\-|\*|Module \d+|Unit \d+|Module \d+:|Unit \d+:)\s*', '', line, flags=re.IGNORECASE)
        line = re.sub(r'\d+\s*[L|l]$', '', line).strip()

        if len(line.split()) < 2:
            continue

        topics.append(line)

    return topics

# 🧹 Gemini output parser
def safe_parse_response(raw_output: str):
    try:
        # Extract content inside code block ```json\n...\n```
        code_block_pattern = re.compile(r"```(?:json|python)?\n(.*?)```", re.DOTALL)
        match = code_block_pattern.search(raw_output)
        clean_output = match.group(1).strip() if match else raw_output.strip()

        # First try parsing as JSON
        try:
            return json.loads(clean_output), None
        except json.JSONDecodeError:
            pass  # fallback below

        # Then fallback to Python literal eval
        return ast.literal_eval(clean_output), None
    except Exception as e:
        return None, str(e)

# 🧠 Main API view
@method_decorator(ratelimit(key='ip', rate='5/h', method='POST', block=True), name='post')
class PlaylistAnalyzeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        playlist_url = request.data.get("playlist_url")
        syllabus = request.data.get("syllabus")

        if not playlist_url or not syllabus:
            return Response(
                {"error": "playlist_url and syllabus are required."},
                status=400
            )

        # Step 1: Extract video titles from playlist
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

        # Step 2: Clean syllabus
        syllabus_topics = extract_syllabus_topics(syllabus)

        # Step 3: Gemini prompt
        prompt = (
            "You are an AI assistant. Match YouTube video titles with syllabus topics only.\n"
            "Return only a VALID JSON list of objects like this:\n"
            '[{"topic": "Binary Trees", "videos": ["Video 1", "Video 2"]}]\n\n'
            "Syllabus Topics:\n" +
            "\n".join(f"- {topic}" for topic in syllabus_topics) + "\n\n" +
            "Video Titles:\n" +
            "\n".join([f"{i+1}. {title}" for i, title in enumerate(video_titles)])
        )

        # Step 4: Gemini API call
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            raw_output = response.text
            print("📥 Gemini raw output:\n", raw_output)

            parsed_response, parse_error = safe_parse_response(raw_output)

            if parsed_response is None:
                return Response({
                    "error": "Gemini API failed or returned invalid output.",
                    "details": f"Parse error: {parse_error}",
                    "raw_output": raw_output
                }, status=500)

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
                "user": request.user.username,
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

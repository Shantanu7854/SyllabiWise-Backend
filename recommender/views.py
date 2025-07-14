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

# Load environment variables
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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

        # Step 2: Prepare Gemini prompt
        prompt = (
            "You are an AI assistant. Match YouTube videos to syllabus topics.\n"
            "Return ONLY a Python-style list of dictionaries. DO NOT add explanations.\n"
            "Each dictionary should have a 'topic' and a 'videos' list.\n\n"
            "Example:\n"
            "[{'topic': 'Optical Fiber', 'videos': ['Intro to Fiber', 'Fiber Types']}]\n\n"
            f"Syllabus:\n{syllabus}\n\n"
            f"Video Titles:\n" +
            "\n".join([f"{i+1}. {title}" for i, title in enumerate(video_titles)])
        )

        # Step 3: Call Gemini API and parse response
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)

            raw_output = response.text
            print("📥 Gemini raw output:\n", raw_output)

            # 🔧 Remove Markdown-style code block if present
            code_block_pattern = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)
            match = code_block_pattern.search(raw_output)
            if match:
                clean_output = match.group(1)
            else:
                clean_output = raw_output

            # Safely parse the structured response
            parsed_response = ast.literal_eval(clean_output.strip())

        except Exception as e:
            raw_output = locals().get("raw_output", "No response generated.")
            return Response({
                "error": "Gemini API failed or returned invalid output.",
                "details": str(e),
                "raw_output": raw_output
            }, status=500)

        # Step 4: Save result to MongoDB
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

        # Step 5: Return clean API response
        return Response({"recommendations": parsed_response}, status=200)

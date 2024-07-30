import os
import logging
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
import httpx
import aiofiles
from datetime import datetime
from dotenv import load_dotenv
from PIL import Image
from PIL.ExifTags import TAGS
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("uvicorn")
logger.setLevel(logging.INFO)

load_dotenv()

app = FastAPI()

WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN")
GRAPH_API_TOKEN = os.getenv("GRAPH_API_TOKEN")

# Create images folder if it doesn't exist
if not os.path.exists("images"):
    os.makedirs("images")
    logger.info("Created 'images' folder")

def get_image_metadata(image_path):
    try:
        with Image.open(image_path) as img:
            exif_data = img._getexif()
            if exif_data:
                metadata = {}
                for tag_id, value in exif_data.items():
                    tag = TAGS.get(tag_id, tag_id)
                    metadata[tag] = str(value)  # Convert all values to strings for JSON serialization
                return metadata
            else:
                return {"error": "No EXIF data found"}
    except Exception as e:
        logger.error(f"Error extracting metadata: {str(e)}")
        return {"error": str(e)}

async def download_and_save_image(image_id: str, filename: str):
    async with httpx.AsyncClient() as client:
        # Get image URL
        media_url_response = await client.get(
            f"https://graph.facebook.com/v18.0/{image_id}",
            headers={"Authorization": f"Bearer {GRAPH_API_TOKEN}"}
        )
        media_url = media_url_response.json()["url"]

        # Download image
        response = await client.get(
            media_url,
            headers={"Authorization": f"Bearer {GRAPH_API_TOKEN}"}
        )

        # Save image
        image_path = os.path.join("images", filename)
        async with aiofiles.open(image_path, "wb") as f:
            await f.write(response.content)
    
    logger.info(f"Image saved: {filename}")
    return image_path

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info(f"Incoming webhook message: {body}")

    message = body.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [{}])[0]
    business_phone_number_id = body.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("metadata", {}).get("phone_number_id")

    if message.get("type") == "text":
        logger.info("Received text message")
    elif message.get("type") == "image":
        image_id = message["image"]["id"]
        mime_type = message["image"]["mime_type"]
        caption = message["image"].get("caption", "No caption")

        file_extension = mime_type.split("/")[1]
        filename = f"image_{datetime.now().timestamp()}.{file_extension}"

        try:
            image_path = await download_and_save_image(image_id, filename)
            metadata = get_image_metadata(image_path)
            
            logger.info(f"Image metadata: {json.dumps(metadata, indent=2)}")

            metadata_str = json.dumps(metadata, indent=2)
            response_message = f"Image received and saved successfully!\nCaption: {caption}\n\nMetadata:\n{metadata_str}"

            async with httpx.AsyncClient() as client:
                # Send confirmation message with metadata
                await client.post(
                    f"https://graph.facebook.com/v18.0/{business_phone_number_id}/messages",
                    headers={"Authorization": f"Bearer {GRAPH_API_TOKEN}"},
                    json={
                        "messaging_product": "whatsapp",
                        "to": message["from"],
                        "text": {"body": response_message},
                        "context": {"message_id": message["id"]},
                    }
                )

                # Mark message as read
                await client.post(
                    f"https://graph.facebook.com/v18.0/{business_phone_number_id}/messages",
                    headers={"Authorization": f"Bearer {GRAPH_API_TOKEN}"},
                    json={
                        "messaging_product": "whatsapp",
                        "status": "read",
                        "message_id": message["id"],
                    }
                )
            logger.info(f"Image processed successfully: {filename}")
        except Exception as e:
            logger.error(f"Error processing image: {e}")

    return {"status": "ok"}
  
  
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge")
):
    logger.info(f"Received verification request: mode={hub_mode}, token={hub_verify_token}, challenge={hub_challenge}")
    if hub_mode == "subscribe" and hub_verify_token == WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verified successfully!")
        return PlainTextResponse(content=hub_challenge)
    logger.warning("Webhook verification failed")
    raise HTTPException(status_code=403, detail="Forbidden")

@app.get("/", response_class=HTMLResponse)
async def root():
    logger.info("Root endpoint accessed")
    return "<pre>Nothing to see here.\nCheckout README.md to start.</pre>"

@app.on_event("startup")
async def startup_event():
    logger.info("Application is starting up")
    logger.info(f"WEBHOOK_VERIFY_TOKEN: {WEBHOOK_VERIFY_TOKEN}")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application is shutting down")

# Add this new endpoint to log environment variables
@app.get("/debug")
async def debug_info():
    logger.info("Debug endpoint accessed")
    return {
        "WEBHOOK_VERIFY_TOKEN": WEBHOOK_VERIFY_TOKEN,
        "GRAPH_API_TOKEN": GRAPH_API_TOKEN[:5] + "..." if GRAPH_API_TOKEN else None,
        "PORT": os.getenv("PORT"),
    }
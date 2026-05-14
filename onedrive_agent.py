import json
import os

import marketing_campaign_agent

PROCESSED_FILES_JSON = "processed_marketing_onedrive_files.json"


def load_processed_files():
    if os.path.exists(PROCESSED_FILES_JSON):
        try:
            with open(PROCESSED_FILES_JSON, "r") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"Could not load processed files JSON: {e}")
            return set()
    return set()


def save_processed_files(processed_set):
    try:
        with open(PROCESSED_FILES_JSON, "w") as f:
            json.dump(list(processed_set), f)
    except Exception as e:
        print(f"Could not save processed files JSON: {e}")


def _model_to_dict(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def poll_folder(graph, folder_path):
    """
    Checks a OneDrive folder for new files, detects marketing product documents,
    and returns generated LinkedIn campaign messages.
    """
    processed = load_processed_files()
    generated_messages = []

    files = graph.list_onedrive_folder(folder_path, recursive=True)
    if not files:
        return generated_messages

    new_files = [file_item for file_item in files if file_item["id"] not in processed]

    if not new_files:
        return generated_messages

    print(f"\nOneDrive Agent: Found {len(new_files)} new file(s) in '{folder_path}'")

    for file_item in new_files:
        file_id = file_item.get("id")
        file_name = file_item.get("name", "unknown")
        download_url = file_item.get("@microsoft.graph.downloadUrl")
        content_type = file_item.get("file", {}).get("mimeType", "")

        if not download_url:
            print(f"   File '{file_name}' has no download URL. Skipping.")
            continue

        print(f"   Downloading: {file_name}")
        file_bytes = graph.download_onedrive_file(download_url)
        if not file_bytes:
            continue
        print(f"   Downloaded {len(file_bytes):,} bytes; content type: {content_type}")

        print(f"   Analyzing '{file_name}' for marketing product content...")
        result = marketing_campaign_agent.analyze_attachment(
            file_bytes, file_name, content_type
        )

        if result is None:
            print(f"   Could not analyze '{file_name}'. It will be retried later.")
            continue

        if result.is_marketing_product_document:
            print(f"   Marketing product document detected in '{file_name}'!")
            print(f"      Product Name  : {result.product_name}")
            print(f"      Audience      : {result.target_audience}")
            print(f"      Campaign Goal : {result.campaign_goal}")
            print("      LinkedIn Message:")
            print(result.linkedin_message or "      N/A")

            generated_messages.append(
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "linkedin_message": result.linkedin_message or "",
                    "analysis": _model_to_dict(result),
                }
            )
        else:
            print(f"   '{file_name}' is not a marketing product document. Skipping.")

        processed.add(file_id)
        save_processed_files(processed)

    return generated_messages

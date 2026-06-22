from image_helper.asset_metadata import extract_asset_metadata


def test_extract_asset_metadata_reads_exif_and_stack() -> None:
    asset = {
        "id": "asset-1",
        "originalFileName": "IMG_0001.jpg",
        "exifInfo": {
            "exifImageWidth": 4032,
            "exifImageHeight": 3024,
        },
        "stack": {
            "id": "stack-1",
            "primaryAssetId": "asset-1",
        },
    }

    metadata = extract_asset_metadata(asset)

    assert metadata["width"] == 4032
    assert metadata["height"] == 3024
    assert metadata["original_file_name"] == "IMG_0001.jpg"
    assert metadata["stack_id"] == "stack-1"
    assert metadata["is_primary_in_stack"] is True


def test_extract_asset_metadata_handles_missing_fields() -> None:
    metadata = extract_asset_metadata({"id": "asset-2"})
    assert metadata["width"] is None
    assert metadata["height"] is None
    assert metadata["stack_id"] is None
    assert metadata["is_primary_in_stack"] is None

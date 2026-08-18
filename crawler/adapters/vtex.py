import json
import re


def extract_vtex_state(html):
    match = re.search(r'<template[^>]*data-varname="__STATE__"[^>]*>(.*?)</template>', html, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except Exception:
        return None


def extract_vtex_events(html):
    match = re.search(r'vtex\.events\.addData\((\{.*?\})\);', html, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except Exception:
        return None


def get_state_product_data(state, ean):
    result = {}

    for key, value in state.items():
        if not isinstance(value, dict):
            continue
        if key.startswith("Product:") and "productName" in value:
            result["name"] = value.get("productName")
            result["description"] = value.get("description")
            brand = value.get("brand")
            if isinstance(brand, str):
                result["brand"] = brand

    for key, value in state.items():
        if not isinstance(value, dict):
            continue
        if value.get("ean") == ean:
            result["ean_found"] = True
            result["item_name"] = value.get("name") or value.get("nameComplete")
            images = value.get("images")
            if isinstance(images, list):
                for i, img in enumerate(images[:4]):
                    if isinstance(img, dict):
                        result[f"image{i+1}"] = img.get("imageUrl")

    for key, value in state.items():
        if not isinstance(value, dict):
            continue
        if value.get("Price") is not None:
            result["price"] = value["Price"]
            result["price_from"] = value.get("ListPrice")
            break

    if "image1" not in result:
        for key, value in state.items():
            if isinstance(value, dict) and "imageUrl" in value:
                img_keys = sorted([k for k in state if isinstance(state[k], dict) and "imageUrl" in state[k]])
                for i, ik in enumerate(img_keys[:4]):
                    result[f"image{i+1}"] = state[ik]["imageUrl"]
                break

    return result

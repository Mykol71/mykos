"""Generic shared popup appearance assets; no host or identity data."""
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse
router=APIRouter()
@router.get('/popup-theme.css',include_in_schema=False)
def stylesheet():
    return FileResponse(Path(__file__).with_name('popup-theme.css'),media_type='text/css',headers={'Cache-Control':'no-cache'})
@router.get('/popup-theme.js',include_in_schema=False)
def javascript():
    return FileResponse(Path(__file__).with_name('popup-theme.js'),media_type='text/javascript',headers={'Cache-Control':'no-cache'})

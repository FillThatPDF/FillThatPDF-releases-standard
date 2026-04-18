"""
PDF Profile Manager - Per-PDF configuration storage system.

This module provides persistent storage of detection settings per-PDF,
preventing regressions when fixing one PDF from breaking others.
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
import pdfplumber


class PDFProfileManager:
    """Manages per-PDF detection profiles to prevent regression."""
    
    # v23.77: Improved image box detection with corrected coordinates.
    DETECTOR_VERSION = "23.77"
    
    def __init__(self, profiles_dir: Optional[str] = None):
        """
        Initialize the profile manager.
        
        Args:
            profiles_dir: Directory to store profiles (defaults to ~/.FillThatPDF/profiles)
        """
        if profiles_dir:
            self.profiles_dir = Path(profiles_dir)
        else:
            # Default to user home directory
            self.profiles_dir = Path.home() / '.FillThatPDF' / 'profiles'
        
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        
    def _get_pdf_hash(self, pdf_path: str) -> str:
        """
        Generate a unique hash for a PDF file.
        Uses file content hash + file size for uniqueness.
        """
        pdf_path = Path(pdf_path)
        
        # Read first 8KB and last 8KB for quick hashing
        file_size = pdf_path.stat().st_size
        hasher = hashlib.md5()
        
        with open(pdf_path, 'rb') as f:
            # First 8KB
            hasher.update(f.read(8192))
            
            # Last 8KB if file is larger
            if file_size > 16384:
                f.seek(-8192, 2)
                hasher.update(f.read())
            
            # Add file size to hash
            hasher.update(str(file_size).encode())
        
        return hasher.hexdigest()[:16]  # Use first 16 chars for brevity
    
    def _get_profile_path(self, pdf_path: str) -> Path:
        """Get the profile file path for a PDF."""
        pdf_hash = self._get_pdf_hash(pdf_path)
        pdf_name = Path(pdf_path).stem[:50]  # Limit filename length
        return self.profiles_dir / f"{pdf_name}_{pdf_hash}.json"
    
    def has_profile(self, pdf_path: str) -> bool:
        """Check if a profile exists for this PDF."""
        return self._get_profile_path(pdf_path).exists()
    
    def load_profile(self, pdf_path: str) -> Optional[Dict[str, Any]]:
        """
        Load a profile for a PDF.
        
        Returns:
            Dict with profile data, or None if no profile exists
        """
        profile_path = self._get_profile_path(pdf_path)
        
        if not profile_path.exists():
            return None
        
        try:
            with open(profile_path, 'r') as f:
                profile = json.load(f)
            
            # v23.75: Force re-calibration if engine version has changed
            if profile.get('version') != self.DETECTOR_VERSION:
                print(f"⚠️  Profile version mismatch ({profile.get('version')} vs {self.DETECTOR_VERSION}). Forcing re-calibration.")
                return None
            
            print(f"📋 Loaded profile: {profile_path.name}")
            print(f"   → Fields detected: {profile.get('field_count', 'unknown')}")
            return profile
        except Exception as e:
            print(f"⚠️  Error loading profile: {e}")
            return None
    
    def save_profile(self, pdf_path: str, settings: Dict[str, Any], 
                     field_count: int, processing_time: float = 0) -> None:
        """
        Save a profile for a PDF.
        
        Args:
            pdf_path: Path to the PDF
            settings: Detection settings used
            field_count: Number of fields detected
            processing_time: Time taken to process (optional)
        """
        profile_path = self._get_profile_path(pdf_path)
        
        # Get PDF metadata
        try:
            with pdfplumber.open(pdf_path) as pdf:
                num_pages = len(pdf.pages)
        except:
            num_pages = 0
        
        profile = {
            'pdf_name': Path(pdf_path).name,
            'pdf_hash': self._get_pdf_hash(pdf_path),
            'num_pages': num_pages,
            'field_count': field_count,
            'settings': settings,
            'processing_time': processing_time,
            'version': self.DETECTOR_VERSION
        }
        
        try:
            with open(profile_path, 'w') as f:
                json.dump(profile, f, indent=2)
            print(f"💾 Saved profile: {profile_path.name}")
        except Exception as e:
            print(f"⚠️  Error saving profile: {e}")
    
    def get_recommended_settings(self, pdf_path: str) -> Optional[Dict[str, Any]]:
        """
        Get recommended settings from a profile.
        
        Returns:
            Settings dict or None if no profile
        """
        profile = self.load_profile(pdf_path)
        if profile and 'settings' in profile:
            return profile['settings']
        return None
    
    def list_profiles(self) -> list:
        """List all saved profiles."""
        profiles = []
        for profile_file in self.profiles_dir.glob('*.json'):
            try:
                with open(profile_file, 'r') as f:
                    profile = json.load(f)
                profiles.append({
                    'name': profile.get('pdf_name', 'Unknown'),
                    'fields': profile.get('field_count', 0),
                    'file': profile_file.name
                })
            except:
                pass
        return profiles
    
    def delete_profile(self, pdf_path: str) -> bool:
        """Delete a profile for a PDF."""
        profile_path = self._get_profile_path(pdf_path)
        if profile_path.exists():
            profile_path.unlink()
            return True
        return False
    
    def validate_profile(self, pdf_path: str) -> bool:
        """
        Check if the profile is still valid for this PDF.
        Returns False if PDF has changed (hash mismatch).
        """
        profile_path = self._get_profile_path(pdf_path)
        
        if not profile_path.exists():
            return False
        
        try:
            with open(profile_path, 'r') as f:
                profile = json.load(f)
            
            current_hash = self._get_pdf_hash(pdf_path)
            stored_hash = profile.get('pdf_hash', '')
            
            return current_hash == stored_hash
        except:
            return False


# Export singleton for easy access
_profile_manager = None

def get_profile_manager(profiles_dir: Optional[str] = None) -> PDFProfileManager:
    """Get the global profile manager instance."""
    global _profile_manager
    if _profile_manager is None or profiles_dir:
        _profile_manager = PDFProfileManager(profiles_dir)
    return _profile_manager
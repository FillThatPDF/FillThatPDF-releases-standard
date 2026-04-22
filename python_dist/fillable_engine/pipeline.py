"""
Pipeline Orchestrator

Wires together the five phases:
  Phase 1:   Analyze (PageAnalyzer)
  Phase 2:   Detect (all detectors)
  Phase 3:   Resolve (UnifiedResolver)
  Phase 3.5: Post-process (label enrichment, radio groups, height, snapping)
  Phase 4:   Write (PDFWriter)
"""

import time
from pathlib import Path
from typing import List, Dict, Optional, Type

from .models import PageModel, FieldCandidate, ResolvedField
from .page_analyzer import PageAnalyzer
from .resolver import UnifiedResolver
from .writer import PDFWriter
from .detectors.base import BaseDetector
from .calibration import auto_calibrate


class Pipeline:
    """Main orchestrator — runs the full detection pipeline."""

    def __init__(self, input_pdf: str, output_pdf: str = None,
                 settings: Dict = None, pages: str = None):
        self.input_pdf = Path(input_pdf)
        self.settings = settings or {}

        # Output path
        suffix = self.settings.get('output_suffix', '_fillable')
        if output_pdf:
            self.output_pdf = Path(output_pdf)
        else:
            self.output_pdf = self.input_pdf.parent / f"{self.input_pdf.stem}{suffix}.pdf"

        # Parse target pages (1-based string like "1,3-5" → 0-based set)
        self.target_pages = self._parse_pages(pages) if pages else None

        # Registry of detector classes
        self._detector_classes: List[Type[BaseDetector]] = []

        # Registry of post-processor classes (instantiated at run time)
        self._postprocessor_classes: List[Type] = []

    def register_detector(self, detector_cls: Type[BaseDetector]):
        """Register a detector class to run during Phase 2."""
        self._detector_classes.append(detector_cls)

    def register_detectors(self, detector_classes: List[Type[BaseDetector]]):
        """Register multiple detector classes."""
        self._detector_classes.extend(detector_classes)

    def register_postprocessors(self, pp_classes: List[Type]):
        """Register post-processor classes to run during Phase 3.5."""
        self._postprocessor_classes.extend(pp_classes)

    def run(self) -> str:
        """
        Run the full pipeline: Analyze → Detect → Resolve → Post-process → Write.

        Returns:
            Path to the output PDF.
        """
        start = time.time()
        print(f"\n{'='*60}")
        print(f"FillThatPDF v24 — Modular Engine")
        print(f"Input:  {self.input_pdf}")
        print(f"Output: {self.output_pdf}")
        print(f"{'='*60}\n")

        # Phase 1: ANALYZE
        print("Phase 1: Analyzing page structure...")
        t1 = time.time()
        analyzer = PageAnalyzer(self.settings)
        pages = analyzer.analyze(str(self.input_pdf), self.target_pages)
        print(f"   Phase 1 complete ({time.time() - t1:.2f}s)\n")

        if not pages:
            print("   No pages to process!")
            return str(self.output_pdf)

        # Phase 1.5: AUTO-CALIBRATE (optional)
        if self.settings.get('auto_calibrate', False):
            print("Phase 1.5: Auto-calibrating detection settings...")
            t15 = time.time()
            auto_calibrate(pages, self.settings)
            print(f"   Phase 1.5 complete ({time.time() - t15:.2f}s)\n")

        # Phase 2: DETECT
        print(f"Phase 2: Running {len(self._detector_classes)} detectors...")
        t2 = time.time()
        all_candidates: List[FieldCandidate] = []

        for det_cls in self._detector_classes:
            det = det_cls(self.settings)
            det_name = det_cls.__name__
            try:
                candidates = det.detect(pages)
                all_candidates.extend(candidates)
                print(f"   {det_name}: {len(candidates)} candidates")
            except Exception as e:
                print(f"   {det_name}: ERROR - {e}")

        print(f"   Total: {len(all_candidates)} candidates ({time.time() - t2:.2f}s)\n")

        # Phase 3: RESOLVE
        print("Phase 3: Resolving conflicts (single pass)...")
        t3 = time.time()
        resolver = UnifiedResolver(self.settings)
        resolved = resolver.resolve(all_candidates, pages)
        print(f"   Phase 3 complete: {len(resolved)} fields committed ({time.time() - t3:.2f}s)\n")

        # Phase 3.5: POST-PROCESS
        if self._postprocessor_classes:
            print(f"Phase 3.5: Running {len(self._postprocessor_classes)} post-processors...")
            t35 = time.time()
            for pp_cls in self._postprocessor_classes:
                pp_name = pp_cls.__name__
                try:
                    pp = pp_cls(self.settings) if self._accepts_settings(pp_cls) else pp_cls()
                    resolved = pp.process(resolved, pages)
                    print(f"   {pp_name}: done ({len(resolved)} fields)")
                except Exception as e:
                    print(f"   {pp_name}: ERROR - {e}")
            print(f"   Phase 3.5 complete ({time.time() - t35:.2f}s)\n")

        # Phase 4: WRITE
        print("Phase 4: Creating fillable PDF...")
        t4 = time.time()
        writer = PDFWriter(self.settings)

        # Build rotation/mediabox maps from page models
        page_rotations = {p.page_num: p.rotation for p in pages}
        page_mediaboxes = {p.page_num: p.mediabox for p in pages}

        writer.write(resolved, str(self.input_pdf), str(self.output_pdf),
                     page_rotations, page_mediaboxes)
        print(f"   Phase 4 complete ({time.time() - t4:.2f}s)\n")

        # Summary
        elapsed = time.time() - start
        print(f"{'='*60}")
        print(f"Done in {elapsed:.2f}s -- {len(resolved)} fields -> {self.output_pdf}")
        print(f"{'='*60}\n")

        # Print field summary by page
        from collections import Counter
        page_counts = Counter(f.page for f in resolved)
        for pg in sorted(page_counts.keys()):
            print(f"   Page {pg + 1}: {page_counts[pg]} fields")

        return str(self.output_pdf)

    @staticmethod
    def _accepts_settings(cls) -> bool:
        """Check if a post-processor's __init__ accepts a settings argument."""
        import inspect
        try:
            sig = inspect.signature(cls.__init__)
            params = list(sig.parameters.keys())
            return 'settings' in params
        except (ValueError, TypeError):
            return False

    def _parse_pages(self, pages_str: str) -> Optional[List[int]]:
        """Parse page range string (1-based) to list of 0-based page indices."""
        if not pages_str:
            return None

        indices = set()
        for part in pages_str.split(','):
            part = part.strip()
            if '-' in part:
                try:
                    start, end = part.split('-', 1)
                    for p in range(int(start), int(end) + 1):
                        indices.add(p - 1)  # Convert to 0-based
                except ValueError:
                    pass
            else:
                try:
                    indices.add(int(part) - 1)
                except ValueError:
                    pass

        return sorted(indices) if indices else None

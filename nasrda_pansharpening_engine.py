"""
=============================================================================
NASRDA Pan-Sharpening Engine
National Space Research and Development Agency (NASRDA)
Version: 2.0.0
=============================================================================
Single-purpose automated pan-sharpening pipeline.

Inputs  : (1) Low-resolution multispectral image  (.tif)
          (2) High-resolution panchromatic image   (.tif)

Pipeline:
  Stage 1 — Alignment   : Resample multispectral bands to PAN pixel grid
  Stage 2 — Separation  : Decompose MS colour channels (IHS or Brovey)
  Stage 3 — Fusion      : Substitute/scale with PAN band
  Stage 4 — Export      : Write high-resolution colour-fused GeoTIFF

Supported methods : IHS (Intensity-Hue-Saturation)
                    Brovey Transform
                    Gram-Schmidt (spectral sharpening)

Compatible sensors: NigeriaSat-2  (2.5 m PAN / 5 m MS, 4-band)
                    NigeriaSat-X  (22 m MS — PAN-sharpened externally)
                    Sentinel-2    (10 m / 20 m bands)
                    Landsat-8/9   (15 m PAN / 30 m MS)
=============================================================================
"""

import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Tuple, Union

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject as rio_reproject


# ---------------------------------------------------------------------------
# Logging — pan-sharpening steps only
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s]  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("NASRDA.PanSharpening")

STAGE = {
    1: "[ STAGE 1 — ALIGNMENT  ]",
    2: "[ STAGE 2 — SEPARATION ]",
    3: "[ STAGE 3 — FUSION     ]",
    4: "[ STAGE 4 — EXPORT     ]",
}

def log(stage: int, msg: str) -> None:
    logger.info(f"{STAGE[stage]}  {msg}")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class PanSharpenConfig:
    """
    All tunable parameters for the pan-sharpening pipeline.
    Defaults are set for NigeriaSat-2 / Sentinel-2 compatibility.
    """
    method: Literal["ihs", "brovey", "gram_schmidt"] = "brovey"
    # Resampling kernel used when upscaling the multispectral image
    resample_kernel: Resampling = Resampling.bilinear
    # Output directory
    output_dir: Path = Path("./nasrda_pansharp_output")
    # Output compression
    compress: str = "LZW"
    # Clip output values to [0, 1] reflectance range
    clip_output: bool = True
    # Overwrite existing output files
    overwrite: bool = True


# ---------------------------------------------------------------------------
# Stage 1 — Spatial Alignment
# ---------------------------------------------------------------------------
class SpatialAligner:
    """
    Resamples the low-resolution multispectral image to exactly match
    the spatial resolution, extent, and CRS of the panchromatic image.

    This is a mandatory pre-step — pan-sharpening requires the MS and PAN
    arrays to share an identical pixel grid before fusion can occur.
    """

    def __init__(self, config: PanSharpenConfig):
        self.config = config

    def align(
        self,
        ms_path: Path,
        pan_path: Path,
    ) -> Tuple[np.ndarray, np.ndarray, dict]:
        """
        Load both images and resample MS to match PAN grid.

        Returns
        -------
        ms_aligned : np.ndarray  shape (bands, rows, cols)  — resampled MS
        pan        : np.ndarray  shape (1,     rows, cols)  — PAN band
        pan_meta   : dict        — rasterio metadata for the output file
        """
        log(1, f"Opening panchromatic   : {pan_path.name}")
        with rasterio.open(pan_path) as pan_src:
            pan          = pan_src.read().astype(np.float32)
            pan_meta     = pan_src.meta.copy()
            pan_transform = pan_src.transform
            pan_crs      = pan_src.crs
            pan_H, pan_W = pan_src.height, pan_src.width

        log(1, f"PAN grid               : {pan_W} x {pan_H} px  |  CRS: {pan_crs}")

        log(1, f"Opening multispectral  : {ms_path.name}")
        with rasterio.open(ms_path) as ms_src:
            ms_raw       = ms_src.read().astype(np.float32)
            ms_bands     = ms_src.count
            ms_crs       = ms_src.crs
            ms_transform = ms_src.transform
            ms_H, ms_W   = ms_src.height, ms_src.width

        log(1, f"MS  grid               : {ms_W} x {ms_H} px  |  {ms_bands} band(s)  |  CRS: {ms_crs}")

        # Allocate target array
        ms_aligned = np.zeros((ms_bands, pan_H, pan_W), dtype=np.float32)

        has_crs = (pan_crs is not None) and (ms_crs is not None)

        if has_crs:
            log(1, f"Resampling MS to PAN grid ({self.config.resample_kernel.name})...")
            for b in range(ms_bands):
                rio_reproject(
                    source=ms_raw[b],
                    destination=ms_aligned[b],
                    src_transform=ms_transform,
                    src_crs=ms_crs,
                    dst_transform=pan_transform,
                    dst_crs=pan_crs,
                    resampling=self.config.resample_kernel,
                )
        else:
            log(1, "No CRS detected - using pixel-grid resize for alignment...")
            try:
                from PIL import Image as _PILImage
                for b in range(ms_bands):
                    band_img = _PILImage.fromarray(ms_raw[b])
                    resized  = band_img.resize((pan_W, pan_H), _PILImage.BILINEAR)
                    ms_aligned[b] = np.array(resized, dtype=np.float32)
            except ImportError:
                # PIL not available - use numpy zoom
                import scipy.ndimage as _nd
                for b in range(ms_bands):
                    zy = pan_H / ms_raw[b].shape[0]
                    zx = pan_W / ms_raw[b].shape[1]
                    ms_aligned[b] = _nd.zoom(ms_raw[b], (zy, zx), order=1).astype(np.float32)
            log(1, "Pixel-grid resize complete.")
            log(1, "NOTE: No geographic CRS - output will not have map coordinates.")

        log(1, f"Alignment complete: MS now {pan_W} x {pan_H} px - matches PAN")

        # Normalise both to [0, 1] for algorithm stability
        pan       = self._normalise(pan)
        ms_aligned = self._normalise(ms_aligned)

        return ms_aligned, pan, pan_meta

    @staticmethod
    def _normalise(arr: np.ndarray) -> np.ndarray:
        """Percentile stretch to [0, 1] — avoids sensor-specific DN range issues."""
        lo = np.percentile(arr, 2)
        hi = np.percentile(arr, 98)
        if hi == lo:
            return arr
        return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


# ---------------------------------------------------------------------------
# Stage 2 + 3 — Colour Separation & Fusion
# ---------------------------------------------------------------------------
class PanSharpener:
    """
    Implements three industry-standard pan-sharpening algorithms.

    IHS  (Intensity-Hue-Saturation)
        Works on exactly 3 bands (R, G, B). Converts to IHS colour space,
        replaces the Intensity channel with the histogram-matched PAN band,
        then converts back to RGB. Produces visually natural results.

    Brovey Transform
        Works on any number of bands. Scales each MS band by the ratio of
        PAN to the sum of all MS bands. Fast, simple, well-suited for
        NigeriaSat-2's 4-band imagery.

    Gram-Schmidt (spectral sharpening)
        Orthogonalises the MS band stack, replaces the first component
        (which correlates most with PAN) with the histogram-matched PAN,
        then back-projects. Preserves spectral fidelity better than the
        other two methods.
    """

    def __init__(self, config: PanSharpenConfig):
        self.config = config

    # ── Public entry point ────────────────────────────────────────────────

    def fuse(
        self,
        ms: np.ndarray,   # (B, H, W)  normalised [0,1]
        pan: np.ndarray,  # (1, H, W)  normalised [0,1]
    ) -> np.ndarray:
        """Dispatch to the configured algorithm and return fused array."""
        method = self.config.method.lower()
        if method == "ihs":
            return self._ihs(ms, pan)
        elif method == "brovey":
            return self._brovey(ms, pan)
        elif method == "gram_schmidt":
            return self._gram_schmidt(ms, pan)
        else:
            raise ValueError(f"Unknown method: '{method}'. Choose ihs | brovey | gram_schmidt")

    # ── Algorithm 1: IHS ─────────────────────────────────────────────────

    def _ihs(self, ms: np.ndarray, pan: np.ndarray) -> np.ndarray:
        """
        IHS pan-sharpening (3-band only).
        If MS has more than 3 bands, uses bands 0,1,2 as R,G,B and
        appends remaining bands sharpened via Brovey.
        """
        log(2, "IHS — extracting Intensity channel from R,G,B…")

        R, G, B = ms[0], ms[1], ms[2]

        # Forward IHS transform (approximate, computationally stable)
        I = (R + G + B) / 3.0
        V1 = (-R / np.sqrt(6)) - (G / np.sqrt(6)) + (2 * B / np.sqrt(6))
        V2 = (R / np.sqrt(2)) - (G / np.sqrt(2))

        log(3, "IHS — substituting Intensity with histogram-matched PAN…")
        pan_matched = self._histogram_match(pan[0], I)

        # Inverse IHS: reconstruct RGB with PAN intensity
        delta = pan_matched - I
        R_sharp = R + delta
        G_sharp = G + delta
        B_sharp = B + delta

        fused = np.stack([R_sharp, G_sharp, B_sharp], axis=0)

        # Handle extra bands (4th band NIR etc.) via Brovey
        if ms.shape[0] > 3:
            log(3, f"IHS — fusing {ms.shape[0]-3} additional band(s) via Brovey…")
            extra = self._brovey(ms[3:], pan)
            fused = np.concatenate([fused, extra], axis=0)

        return fused

    # ── Algorithm 2: Brovey Transform ────────────────────────────────────

    def _brovey(self, ms: np.ndarray, pan: np.ndarray) -> np.ndarray:
        """
        Brovey Transform — works on any number of bands.
        Formula: fused_b = (MS_b / sum_of_all_MS_bands) * PAN
        """
        log(2, f"Brovey — computing band-sum across {ms.shape[0]} MS band(s)…")

        band_sum = np.sum(ms, axis=0, keepdims=True)                 # (1, H, W)
        # Avoid division by zero at nodata pixels
        band_sum = np.where(band_sum == 0, 1e-9, band_sum)

        log(3, "Brovey — scaling each band by PAN / band-sum ratio…")
        fused = (ms / band_sum) * pan                                 # (B, H, W)

        return fused

    # ── Algorithm 3: Gram-Schmidt ─────────────────────────────────────────

    def _gram_schmidt(self, ms: np.ndarray, pan: np.ndarray) -> np.ndarray:
        """
        Gram-Schmidt spectral sharpening.
        1. Simulate a low-res PAN from MS bands (weighted mean)
        2. Orthogonalise MS stack using GS process
        3. Replace first GS component with histogram-matched real PAN
        4. Back-project to MS band space
        """
        B, H, W = ms.shape
        flat = ms.reshape(B, -1).T                                    # (N, B)

        log(2, "Gram-Schmidt — simulating synthetic PAN from MS bands…")
        # Equal-weight synthetic PAN (can be tuned per sensor)
        synth_pan = np.mean(flat, axis=1)                             # (N,)

        log(2, "Gram-Schmidt — orthogonalising band stack…")
        # Stack: put synthetic PAN as first column, then MS bands
        data = np.column_stack([synth_pan, flat])                     # (N, B+1)
        gs   = self._gram_schmidt_ortho(data)                         # (N, B+1)

        log(3, "Gram-Schmidt — replacing first component with real PAN…")
        pan_flat = pan[0].ravel()                                      # (N,)
        pan_matched = self._histogram_match(pan_flat, gs[:, 0])

        gs[:, 0] = pan_matched

        # Back-project: inverse GS (multiply by original projection matrix)
        # Simplified: replace synthetic-PAN contribution in each band
        delta = (pan_matched - synth_pan)                              # (N,)
        fused_flat = flat + delta[:, np.newaxis]                       # (N, B)

        fused = fused_flat.T.reshape(B, H, W)
        return fused

    # ── Gram-Schmidt orthogonalisation ───────────────────────────────────

    @staticmethod
    def _gram_schmidt_ortho(data: np.ndarray) -> np.ndarray:
        """
        Classic Gram-Schmidt process applied column-wise.
        data : (N_pixels, N_bands)
        Returns orthogonalised matrix of same shape.
        """
        result = np.zeros_like(data)
        for i in range(data.shape[1]):
            vec = data[:, i].copy()
            for j in range(i):
                proj_num = np.dot(vec, result[:, j])
                proj_den = np.dot(result[:, j], result[:, j])
                if proj_den > 1e-12:
                    vec -= (proj_num / proj_den) * result[:, j]
            result[:, i] = vec
        return result

    # ── Histogram matching ────────────────────────────────────────────────

    @staticmethod
    def _histogram_match(source: np.ndarray, reference: np.ndarray) -> np.ndarray:
        """
        Adjust the histogram of `source` to match `reference`.
        Ensures PAN band has the same mean and standard deviation as the
        intensity channel it replaces — prevents colour distortion.
        """
        src_mean, src_std = source.mean(), source.std()
        ref_mean, ref_std = reference.mean(), reference.std()
        if src_std < 1e-9:
            return source
        matched = (source - src_mean) * (ref_std / src_std) + ref_mean
        return matched


# ---------------------------------------------------------------------------
# Stage 4 — Export
# ---------------------------------------------------------------------------
class PanSharpExporter:
    """Writes the fused high-resolution array to a GeoTIFF."""

    def __init__(self, config: PanSharpenConfig):
        self.config = config
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

    def export(
        self,
        fused: np.ndarray,
        pan_meta: dict,
        filename: str,
    ) -> Path:
        out_path = self.config.output_dir / filename

        if out_path.exists() and not self.config.overwrite:
            raise FileExistsError(f"Output already exists: {out_path}")

        if self.config.clip_output:
            fused = np.clip(fused, 0.0, 1.0)

        # Scale to uint16 for storage efficiency (0–10000 reflectance scale)
        fused_scaled = (fused * 10000).astype(np.uint16)

        write_meta = {
            **pan_meta,
            "driver":  "GTiff",
            "dtype":   "uint16",
            "count":   fused_scaled.shape[0],
            "compress": self.config.compress,
            "nodata":  0,
        }

        log(4, f"Writing fused GeoTIFF  : {out_path.name}")
        log(4, f"  Bands  : {fused_scaled.shape[0]}")
        log(4, f"  Size   : {fused_scaled.shape[2]} × {fused_scaled.shape[1]} px")
        log(4, f"  Dtype  : uint16  |  Scale: ×10000 reflectance units")
        log(4, f"  Compress: {self.config.compress}")

        with rasterio.open(out_path, "w", **write_meta) as dst:
            dst.write(fused_scaled)

        size_mb = out_path.stat().st_size / 1_048_576
        log(4, f"Export complete        : {out_path}  ({size_mb:.1f} MB) ✓")
        return out_path


# ---------------------------------------------------------------------------
# Master Pipeline — ties all four stages together
# ---------------------------------------------------------------------------
class NASRDAPanSharpeningPipeline:
    """
    Automated pan-sharpening pipeline for NASRDA ground station imagery.

    Usage
    -----
        pipeline = NASRDAPanSharpeningPipeline()
        result   = pipeline.run(
            ms_path  = Path("NigeriaSat2_MS_5m.tif"),
            pan_path = Path("NigeriaSat2_PAN_2.5m.tif"),
        )
        print(result["output_file"])

    Or via CLI:
        python nasrda_pansharpening_engine.py MS.tif PAN.tif --method brovey
    """

    def __init__(self, config: PanSharpenConfig | None = None):
        self.config   = config or PanSharpenConfig()
        self.aligner  = SpatialAligner(self.config)
        self.sharpener = PanSharpener(self.config)
        self.exporter  = PanSharpExporter(self.config)

    def run(
        self,
        ms_path:  Union[str, Path],
        pan_path: Union[str, Path],
        output_name: str | None = None,
    ) -> dict:
        """
        Execute the full 4-stage pan-sharpening pipeline.

        Parameters
        ----------
        ms_path     : Path to the low-resolution multispectral GeoTIFF
        pan_path    : Path to the high-resolution panchromatic GeoTIFF
        output_name : Optional output filename (auto-generated if None)

        Returns
        -------
        dict with keys: status, method, output_file, bands,
                        resolution_m, processing_time_s
        """
        ms_path  = Path(ms_path)
        pan_path = Path(pan_path)

        self._banner(ms_path, pan_path)
        t0 = datetime.now()

        # ── Stage 1: Alignment ────────────────────────────────────────────
        ms_aligned, pan, pan_meta = self.aligner.align(ms_path, pan_path)

        # ── Stage 2 + 3: Colour Separation & Fusion ───────────────────────
        log(2, f"Method: {self.config.method.upper()}")
        log(3, "Fusing panchromatic detail into multispectral colour…")
        fused = self.sharpener.fuse(ms_aligned, pan)
        log(3, f"Fusion complete  — output shape: {fused.shape}")

        # ── Stage 4: Export ───────────────────────────────────────────────
        if output_name is None:
            output_name = f"{ms_path.stem}_pansharp_{self.config.method}.tif"

        out_path = self.exporter.export(fused, pan_meta, output_name)

        elapsed = (datetime.now() - t0).total_seconds()
        self._summary(out_path, fused, pan_meta, elapsed)

        return {
            "status":            "success",
            "method":            self.config.method,
            "output_file":       str(out_path),
            "bands":             fused.shape[0],
            "width":             fused.shape[2],
            "height":            fused.shape[1],
            "processing_time_s": round(elapsed, 2),
        }

    # ── Internal helpers ──────────────────────────────────────────────────

    @staticmethod
    def _banner(ms_path: Path, pan_path: Path) -> None:
        print()
        print("=" * 62)
        print("  NASRDA PAN-SHARPENING ENGINE  v2.0.0")
        print("  National Space Research and Development Agency")
        print("=" * 62)
        print(f"  MS   input : {ms_path.name}")
        print(f"  PAN  input : {pan_path.name}")
        print("=" * 62)
        print()

    @staticmethod
    def _summary(out_path: Path, fused: np.ndarray, meta: dict, elapsed: float) -> None:
        print()
        print("=" * 62)
        print("  PIPELINE COMPLETE")
        print("=" * 62)
        print(f"  Output     : {out_path}")
        print(f"  Dimensions : {fused.shape[2]} × {fused.shape[1]} px")
        print(f"  Bands      : {fused.shape[0]}")
        print(f"  CRS        : {meta.get('crs', 'unknown')}")
        print(f"  Runtime    : {elapsed:.2f}s")
        print("=" * 62)
        print()


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="NASRDA Pan-Sharpening Engine — fuse MS + PAN into high-res colour GeoTIFF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python nasrda_pansharpening_engine.py MS_5m.tif PAN_2.5m.tif
  python nasrda_pansharpening_engine.py MS_5m.tif PAN_2.5m.tif --method ihs
  python nasrda_pansharpening_engine.py MS_5m.tif PAN_2.5m.tif --method gram_schmidt --output-dir ./results
        """,
    )
    parser.add_argument("ms",      help="Low-resolution multispectral GeoTIFF path")
    parser.add_argument("pan",     help="High-resolution panchromatic GeoTIFF path")
    parser.add_argument(
        "--method", default="brovey",
        choices=["ihs", "brovey", "gram_schmidt"],
        help="Pan-sharpening algorithm (default: brovey)",
    )
    parser.add_argument(
        "--resample", default="bilinear",
        choices=["bilinear", "bicubic", "nearest", "lanczos"],
        help="Resampling kernel for MS→PAN alignment (default: bilinear)",
    )
    parser.add_argument(
        "--output-dir", default="./nasrda_pansharp_output",
        help="Output directory for fused GeoTIFF (default: ./nasrda_pansharp_output)",
    )
    parser.add_argument(
        "--output-name", default=None,
        help="Custom output filename (optional)",
    )
    parser.add_argument(
        "--no-clip", action="store_true",
        help="Do not clip output values to [0,1] range",
    )
    args = parser.parse_args()

    # Map resample string to rasterio enum
    resample_map = {
        "bilinear": Resampling.bilinear,
        "bicubic":  Resampling.cubic,
        "nearest":  Resampling.nearest,
        "lanczos":  Resampling.lanczos,
    }

    config = PanSharpenConfig(
        method          = args.method,
        resample_kernel = resample_map[args.resample],
        output_dir      = Path(args.output_dir),
        clip_output     = not args.no_clip,
    )

    pipeline = NASRDAPanSharpeningPipeline(config)

    try:
        result = pipeline.run(
            ms_path     = args.ms,
            pan_path    = args.pan,
            output_name = args.output_name,
        )
        print(json.dumps(result, indent=2))
        sys.exit(0)
    except FileNotFoundError as e:
        print(f"\n[ERROR] Input file not found: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Pipeline failed: {e}")
        raise

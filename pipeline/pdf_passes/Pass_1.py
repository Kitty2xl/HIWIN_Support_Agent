import cv2
import numpy as np
import os
import fitz
import onnxruntime as ort
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import itertools
from core.progress import ProgressTracker


# Label map extracted from inference.yml
LAYOUT_CLASSES = {
    0:  "paragraph_title",
    1:  "image",
    2:  "text",
    3:  "number",
    4:  "abstract",
    5:  "content",
    6:  "figure_title",
    7:  "formula",
    8:  "table",
    9:  "reference",
    10: "doc_title",
    11: "footnote",
    12: "header",
    13: "algorithm",
    14: "footer",
    15: "seal",
    16: "chart",
    17: "formula_number",
    18: "aside_text",
    19: "reference_content",
}

_DEFAULT_FONT  = cv2.FONT_HERSHEY_SIMPLEX
_DEFAULT_SCALE = 1.0
_DEFAULT_THICK = 2

# Detection labels that are decorative only.  A page whose detections are all in
# this set (or whose detections are empty) carries no real content, so Pass 2
# can skip the vision call for it.  Everything else (text, titles, tables,
# figures, formulas, etc.) counts as real content.
_NONCONTENT_CLASSES = {"number", "header", "footer"}


def _process_single_box(args, dir_tables, dir_figures, output_prefix, clean_img):
    """Process a single detected box: crop to disk, determine background color, render placeholder."""
    idx, box_type, box_info = args
    x1, y1, x2, y2 = expand_to_background(clean_img, box_info['coordinate'])
    w, h = x2 - x1, y2 - y1
    if w < 10 or h < 10:
        return None
    crop       = clean_img[y1:y2, x1:x2].copy()
    target_dir = dir_tables if box_type == 'table' else dir_figures
    subfolder  = "Tables"  if box_type == 'table' else "Figures"
    crop_filename = f"{output_prefix}_{box_type}_{idx + 1}.jpg"
    crop_path = os.path.join(target_dir, crop_filename)
    cv2.imencode('.jpg', cv2.cvtColor(crop, cv2.COLOR_RGB2BGR),
                 [int(cv2.IMWRITE_JPEG_QUALITY), 85])[1].tofile(crop_path)
    # Determine background colour from the corner pixels surrounding the box
    margin  = 5
    h_img, w_img = clean_img.shape[:2]
    patches = []
    for (py1, py2, px1, px2) in [
        (max(0, y1 - margin), y1,             max(0, x1 - margin), x1),
        (max(0, y1 - margin), y1,             x2, min(w_img, x2 + margin)),
        (y2, min(h_img, y2 + margin),         max(0, x1 - margin), x1),
        (y2, min(h_img, y2 + margin),         x2, min(w_img, x2 + margin)),
    ]:
        if py2 > py1 and px2 > px1:
            patches.append(clean_img[py1:py2, px1:px2])
    if patches:
        all_pixels     = np.vstack([p.reshape(-1, 3) for p in patches if p.size > 0])
        colors, counts = np.unique(all_pixels, axis=0, return_counts=True)
        bg_color       = tuple(int(c) for c in colors[counts.argmax()])
    else:
        bg_color = (255, 255, 255)
    # Build word-wrapped placeholder text
    text  = f"[{subfolder}/{crop_filename}]"
    font, scale, thick = _DEFAULT_FONT, _DEFAULT_SCALE, _DEFAULT_THICK
    max_w = max(10, w - 20)
    lines, current_line = [], ""
    for char in text:
        test_line = current_line + char
        tw, _ = cv2.getTextSize(test_line, font, scale, thick)[0]
        if tw > max_w and current_line:
            lines.append(current_line)
            current_line = char
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)
    _, line_h  = cv2.getTextSize("A", font, scale, thick)[0]
    spacing    = int(line_h * 0.5)
    total_h    = len(lines) * line_h + max(0, len(lines) - 1) * spacing
    start_y    = y1 + (h - total_h) // 2 + line_h
    text_instructions = []
    for line_idx, line in enumerate(lines):
        tw, _ = cv2.getTextSize(line, font, scale, thick)[0]
        text_instructions.append((
            line,
            x1 + (w - tw) // 2,
            start_y + line_idx * (line_h + spacing),
        ))
    # Only return draw instructions; the crop is already written to disk.
    return {
        'rect':  ((x1, y1), (x2, y2), bg_color),
        'texts': text_instructions,
        'font': font, 'scale': scale, 'thick': thick,
    }


def merge_close_boxes(boxes, max_gap=100, align_tolerance=50):
    """
    Iteratively merges bounding boxes that are vertically or horizontally adjacent
    and share similar axis alignments. Handles completely nested boxes naturally.
    """
    if not boxes:
        return []

    merged_boxes = list(boxes)

    while True:
        merged_this_round = False
        next_boxes = []
        skip_indices = set()

        for i in range(len(merged_boxes)):
            if i in skip_indices:
                continue

            current_box = dict(merged_boxes[i])
            box1 = current_box['coordinate']

            for j in range(i + 1, len(merged_boxes)):
                if j in skip_indices:
                    continue

                box2 = merged_boxes[j]['coordinate']

                x_aligned = (
                    abs(box1[0] - box2[0]) <= align_tolerance or
                    abs(box1[2] - box2[2]) <= align_tolerance or
                    max(0, min(box1[2], box2[2]) - max(box1[0], box2[0])) > 0
                )
                y_gap = max(box1[1], box2[1]) - min(box1[3], box2[3])
                vertically_close = y_gap <= max_gap

                y_aligned = (
                    abs(box1[1] - box2[1]) <= align_tolerance or
                    abs(box1[3] - box2[3]) <= align_tolerance or
                    max(0, min(box1[3], box2[3]) - max(box1[1], box2[1])) > 0
                )
                x_gap = max(box1[0], box2[0]) - min(box1[2], box2[2])
                horizontally_close = x_gap <= max_gap

                if (x_aligned and vertically_close) or (y_aligned and horizontally_close):
                    current_box['coordinate'] = [
                        min(box1[0], box2[0]),
                        min(box1[1], box2[1]),
                        max(box1[2], box2[2]),
                        max(box1[3], box2[3]),
                    ]
                    box1 = current_box['coordinate']
                    skip_indices.add(j)
                    merged_this_round = True

            next_boxes.append(current_box)

        merged_boxes = next_boxes
        if not merged_this_round:
            break

    return sorted(merged_boxes, key=lambda b: b['coordinate'][1])


def expand_to_background(img, box, white_threshold=240):
    """Expands a bounding box outward to fully capture its background border colour."""
    x1, y1, x2, y2 = map(int, box)
    h, w = img.shape[:2]

    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)

    if x2 - x1 <= 10 or y2 - y1 <= 10:
        return x1, y1, x2, y2

    margin = 5
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
    cv2.rectangle(mask, (x1 + margin, y1 + margin), (x2 - margin, y2 - margin), 0, -1)

    if cv2.countNonZero(mask) == 0:
        return x1, y1, x2, y2

    border_pixels = img[mask == 255]
    median_color = np.median(border_pixels, axis=0)

    if np.all(median_color > white_threshold):
        return x1, y1, x2, y2

    tolerance = 15
    lower_bound = np.clip(median_color - tolerance, 0, 255).astype(np.uint8)
    upper_bound = np.clip(median_color + tolerance, 0, 255).astype(np.uint8)

    color_mask = cv2.inRange(img, lower_bound, upper_bound)
    cnts, _ = cv2.findContours(color_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    expanded_box = [x1, y1, x2, y2]
    for c in cnts:
        cx, cy, cw, ch = cv2.boundingRect(c)
        if cx <= x2 and cx + cw >= x1 and cy <= y2 and cy + ch >= y1:
            expanded_box[0] = min(expanded_box[0], cx)
            expanded_box[1] = min(expanded_box[1], cy)
            expanded_box[2] = max(expanded_box[2], cx + cw)
            expanded_box[3] = max(expanded_box[3], cy + ch)

    return tuple(expanded_box)


def overlaps_with_table(fig_box, table_boxes, threshold=0.7):
    """Returns True if a figure box significantly overlaps with any table box."""
    fx1, fy1, fx2, fy2 = fig_box
    fig_area = (fx2 - fx1) * (fy2 - fy1)
    if fig_area == 0:
        return False
    for t in table_boxes:
        tx1, ty1, tx2, ty2 = t['coordinate']
        ix1, iy1 = max(fx1, tx1), max(fy1, ty1)
        ix2, iy2 = min(fx2, tx2), min(fy2, ty2)
        if ix2 > ix1 and iy2 > iy1:
            overlap = (ix2 - ix1) * (iy2 - iy1)
            table_area = (tx2 - tx1) * (ty2 - ty1)
            smaller_area = min(fig_area, table_area)
            if smaller_area > 0 and overlap / smaller_area >= threshold:
                return True
    return False


def _run_inference(session, img_rgb, score_threshold=0.5):
    """
    Run a single forward pass of the ONNX layout detection model.

    Accepts an RGB numpy image (H x W x 3 uint8), preprocesses it in-memory,
    runs inference, and returns detections in {'boxes': [...]} format.
    ONNX Runtime's InferenceSession.run() is thread-safe and may be called
    concurrently from multiple workers.
    """
    orig_h, orig_w = img_rgb.shape[:2]
    target_h, target_w = 800, 800

    img = cv2.resize(img_rgb, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    img = img.astype(np.float32) / 255.0
    img = img.transpose((2, 0, 1))
    input_data = np.expand_dims(img, axis=0)

    input_feed = {
        "image":        input_data,
        "im_shape":     np.array([[target_h, target_w]], dtype=np.float32),
        "scale_factor": np.array([[target_h / orig_h, target_w / orig_w]], dtype=np.float32),
    }

    detections = session.run(None, input_feed)[0]

    boxes = []
    for det in detections:
        class_id, score, xmin, ymin, xmax, ymax = det
        if score < score_threshold or class_id < 0:
            continue

        class_name = LAYOUT_CLASSES.get(int(class_id), f"unknown_{int(class_id)}")

        if xmax <= 1.0 and ymax <= 1.0 and (xmax - xmin) < 1.0:
            xmin, ymin = xmin * orig_w, ymin * orig_h
            xmax, ymax = xmax * orig_w, ymax * orig_h

        boxes.append({
            'label':      class_name,
            'coordinate': [float(xmin), float(ymin), float(xmax), float(ymax)],
        })

    return {'boxes': boxes}


def _process_page_from_image(session, img_rgb, output_directory, output_prefix="page",
                              score_threshold=0.5):
    """
    Process a single pre-rendered page image: run two-pass layout detection, crop
    figures and tables to disk, and render inline red placeholder text.

    Accepts a numpy RGB array directly (not a fitz Page) so that multiple pages
    can be safely dispatched to worker threads.  PyMuPDF rendering, which is not
    thread-safe, is done in the main thread before this function is called.
    """
    img = img_rgb

    dir_figures = os.path.join(output_directory, "Cropped_Figure")
    dir_tables  = os.path.join(output_directory, "Cropped_Table")
    dir_pages   = os.path.join(output_directory, "Processed_Page")

    # -------------------------------------------------------------------------
    # Pass A: Detect figures on the original image (in-memory, no temp file)
    # -------------------------------------------------------------------------
    result_a = _run_inference(session, img, score_threshold)

    clean_img = img.copy()
    for box_info in result_a['boxes']:
        if box_info['label'] in ('header', 'number'):
            x1, y1, x2, y2 = map(int, box_info['coordinate'])
            pad = 5
            cv2.rectangle(
                clean_img,
                (max(0, x1 - pad), max(0, y1 - pad)),
                (min(img.shape[1], x2 + pad), min(img.shape[0], y2 + pad)),
                (255, 255, 255), -1,
            )

    figure_boxes  = [b for b in result_a['boxes'] if b['label'] in ('image', 'chart')]
    figure_boxes  = merge_close_boxes(figure_boxes, max_gap=25, align_tolerance=50)
    table_boxes_a = [b for b in result_a['boxes'] if b['label'] == 'table']
    safe_to_blank = [b for b in figure_boxes
                     if not overlaps_with_table(b['coordinate'], table_boxes_a)]

    # -------------------------------------------------------------------------
    # Pass B: Blank safe figures, then re-detect tables on the cleaner image
    # -------------------------------------------------------------------------
    blanked_img = clean_img.copy()
    for box_info in safe_to_blank:
        x1, y1, x2, y2 = expand_to_background(clean_img, box_info['coordinate'])
        cv2.rectangle(blanked_img, (x1, y1), (x2, y2), (255, 255, 255), -1)

    result_b = _run_inference(session, blanked_img, score_threshold)

    table_boxes     = [b for b in result_b['boxes'] if b['label'] == 'table']
    all_table_boxes = table_boxes_a + table_boxes
    figure_boxes    = [b for b in figure_boxes
                       if not overlaps_with_table(b['coordinate'], all_table_boxes)]

    # -------------------------------------------------------------------------
    # Crop all detected elements and build placeholder overlays on the page image
    # -------------------------------------------------------------------------
    task_args = (
        [(i, 'table',  b) for i, b in enumerate(table_boxes)] +
        [(i, 'figure', b) for i, b in enumerate(figure_boxes)]
    )

    with ThreadPoolExecutor() as executor:
        box_results = list(executor.map(
            _process_single_box, task_args,
            itertools.repeat(dir_tables), itertools.repeat(dir_figures),
            itertools.repeat(output_prefix), itertools.repeat(clean_img),
        ))

    valid_results = [r for r in box_results if r is not None]

    # Two-pass render: fill rectangles first, then overlay text
    for draw_info in valid_results:
        pt1, pt2, color = draw_info['rect']
        cv2.rectangle(img, pt1, pt2, color, -1)
    for draw_info in valid_results:
        font, scale, thick = draw_info['font'], draw_info['scale'], draw_info['thick']
        for line, tx, ty in draw_info['texts']:
            cv2.putText(img, line, (tx, ty), font, scale, (255, 0, 0), thick, cv2.LINE_AA)

    page_out = os.path.join(dir_pages, f"{output_prefix}_filled_page.jpg")
    cv2.imencode('.jpg', cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                 [int(cv2.IMWRITE_JPEG_QUALITY), 85])[1].tofile(page_out)

    # Blank-page marker: if the layout model found no real content on this page
    # (only decorative elements, or nothing at all), drop a sibling ".blank"
    # marker so Pass 2 can skip the expensive vision call.  Clear any stale
    # marker when content *is* present, so re-runs stay correct.
    detected = ({b['label'] for b in result_a['boxes']}
                | {b['label'] for b in result_b['boxes']})
    marker_path = os.path.splitext(page_out)[0] + ".blank"
    if detected.issubset(_NONCONTENT_CLASSES):
        with open(marker_path, "w", encoding="utf-8") as f:
            f.write(",".join(sorted(detected)))   # presence = blank; contents for audit
    elif os.path.exists(marker_path):
        os.remove(marker_path)


def pass_1(pdf_path, excluded_pages, output_directory, model_path,
           score_threshold=0.5, checkpoint_manager=None, checkpoint_filename=None,
           batch_size=4, pdf_workers=1, ort_intra_threads=None,
           progress_callback=None, included_pages=None, render_dpi=200):
    """
    Pass 1: Layout Detection.

    Renders each PDF page at 300 DPI, runs two-pass in-memory ONNX layout detection
    to locate figures and tables, crops each element to disk, and produces processed
    page images with inline red placeholder text for downstream transcription.

    Batching strategy
    -----------------
    Pages are rendered sequentially in the main thread (PyMuPDF is not thread-safe),
    then dispatched in batches of `batch_size` to a persistent ThreadPoolExecutor for
    concurrent ONNX inference and image processing.  A single executor instance is
    reused across all batches to avoid repeated thread-pool spin-up overhead.

    ORT's intra-op thread count is scaled down proportionally so that `batch_size`
    concurrent session.run() calls share the available CPU cores evenly rather than
    all competing for every core simultaneously.
    """
    if checkpoint_manager and checkpoint_filename and \
            checkpoint_manager.is_done(checkpoint_filename, "pass_1"):
        return []

    excluded_set = set(excluded_pages or [])
    included_set = set(included_pages or [])   # when non-empty, overrides excluded_set

    os.makedirs(os.path.join(output_directory, "Cropped_Figure"), exist_ok=True)
    os.makedirs(os.path.join(output_directory, "Cropped_Table"),  exist_ok=True)
    os.makedirs(os.path.join(output_directory, "Processed_Page"), exist_ok=True)

    sess_opts = ort.SessionOptions()
    sess_opts.inter_op_num_threads = 1
    if ort_intra_threads is not None:
        # Explicit override from caller — use it directly.
        sess_opts.intra_op_num_threads = max(1, ort_intra_threads)
    else:
        # Auto: share CPU evenly across batch_size concurrent ORT calls per PDF
        # and pdf_workers PDFs running Pass 1 simultaneously.
        sess_opts.intra_op_num_threads = max(1, (os.cpu_count() or 4) // (batch_size * pdf_workers))
    session = ort.InferenceSession(
        model_path,
        providers=['CPUExecutionProvider'],
        sess_options=sess_opts,
    )

    with fitz.open(pdf_path) as doc:
        cat = doc.pdf_catalog()
        doc.xref_set_key(cat, "StructTreeRoot", "null")

        if included_set:
            # Include list takes precedence: keep only these pages (in order,
            # ignoring any out-of-range values), excluded_set is ignored.
            page_nums = [pn for pn in range(len(doc)) if pn in included_set]
        else:
            page_nums = [pn for pn in range(len(doc)) if pn not in excluded_set]

        # One persistent executor covers all batches to avoid repeated spin-up cost.
        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            with ProgressTracker(
                total=len(page_nums),
                desc=f"Detecting Layouts (batch={batch_size})",
                pass_name="pass_1",
                callback=progress_callback,
                unit="page",
                mininterval=0.5,
            ) as pbar:
                for batch_start in range(0, len(page_nums), batch_size):
                    batch_page_nums = page_nums[batch_start : batch_start + batch_size]

                    # Render this batch in the main thread (PyMuPDF is not thread-safe)
                    rendered_batch = []
                    for pn in batch_page_nums:
                        pix = doc[pn].get_pixmap(dpi=render_dpi)
                        arr = np.frombuffer(
                            pix.samples, dtype=np.uint8
                        ).reshape(pix.h, pix.w, pix.n).copy()
                        if pix.n == 4:
                            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2RGB)
                        rendered_batch.append((pn, arr))

                    # Process this batch concurrently (ONNX inference + crop + overlay)
                    futures = {
                        executor.submit(
                            _process_page_from_image,
                            session, arr, output_directory,
                            f"page{pn + 1}", score_threshold,
                        ): pn
                        for pn, arr in rendered_batch
                    }
                    for f in as_completed(futures):
                        f.result()  # re-raise any worker exception immediately
                        pbar.update(1)

    if checkpoint_manager and checkpoint_filename:
        checkpoint_manager.mark_done(checkpoint_filename, "pass_1")

    return []
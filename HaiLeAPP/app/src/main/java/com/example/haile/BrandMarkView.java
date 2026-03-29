package com.example.haile;

import android.content.Context;
import android.graphics.BlurMaskFilter;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.util.AttributeSet;
import android.util.TypedValue;
import android.view.View;

import androidx.annotation.Nullable;

public class BrandMarkView extends View {

    private final Paint ambientGlowPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint tileShadowPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint tilePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint tileGlossPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint tileStrokePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint glyphOutlinePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint glyphMainPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint glyphInnerPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint glyphShimmerPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint flarePaint = new Paint(Paint.ANTI_ALIAS_FLAG);

    private final RectF tileRect = new RectF();
    private final RectF glyphRect = new RectF();
    private final Path tilePath = new Path();
    private final Path glyphPath = new Path();
    private final Matrix shaderMatrix = new Matrix();

    private float tileRadius;
    private float glyphOutlineWidth;
    private float glyphMainWidth;
    private float glyphInnerWidth;
    private float glowRadius;
    private float centerX;
    private float centerY;
    private float shimmerProgress = 0f;
    private float pulseProgress = 0f;
    private float ringRotation = 0f;

    public BrandMarkView(Context context) {
        super(context);
        init();
    }

    public BrandMarkView(Context context, @Nullable AttributeSet attrs) {
        super(context, attrs);
        init();
    }

    public BrandMarkView(Context context, @Nullable AttributeSet attrs, int defStyleAttr) {
        super(context, attrs, defStyleAttr);
        init();
    }

    private void init() {
        setLayerType(LAYER_TYPE_SOFTWARE, null);

        ambientGlowPaint.setMaskFilter(new BlurMaskFilter(dp(20f), BlurMaskFilter.Blur.NORMAL));

        tileShadowPaint.setColor(Color.argb(22, 89, 150, 185));
        tileShadowPaint.setMaskFilter(new BlurMaskFilter(dp(18f), BlurMaskFilter.Blur.NORMAL));

        tileStrokePaint.setStyle(Paint.Style.STROKE);
        tileStrokePaint.setStrokeWidth(dp(1.2f));
        tileStrokePaint.setColor(Color.argb(154, 201, 223, 237));

        glyphOutlinePaint.setStyle(Paint.Style.STROKE);
        glyphOutlinePaint.setStrokeCap(Paint.Cap.ROUND);
        glyphOutlinePaint.setStrokeJoin(Paint.Join.ROUND);
        glyphOutlinePaint.setColor(Color.argb(228, 250, 253, 255));

        glyphMainPaint.setStyle(Paint.Style.STROKE);
        glyphMainPaint.setStrokeCap(Paint.Cap.ROUND);
        glyphMainPaint.setStrokeJoin(Paint.Join.ROUND);

        glyphInnerPaint.setStyle(Paint.Style.STROKE);
        glyphInnerPaint.setStrokeCap(Paint.Cap.ROUND);
        glyphInnerPaint.setStrokeJoin(Paint.Join.ROUND);

        glyphShimmerPaint.setStyle(Paint.Style.STROKE);
        glyphShimmerPaint.setStrokeCap(Paint.Cap.ROUND);
        glyphShimmerPaint.setStrokeJoin(Paint.Join.ROUND);
    }

    @Override
    protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        super.onSizeChanged(w, h, oldw, oldh);

        float size = Math.min(w, h);
        float tileInset = size * 0.08f;
        tileRadius = size * 0.17f;
        tileRect.set(
                (w - size) / 2f + tileInset,
                (h - size) / 2f + tileInset,
                (w + size) / 2f - tileInset,
                (h + size) / 2f - tileInset
        );
        centerX = tileRect.centerX();
        centerY = tileRect.centerY();
        glowRadius = size * 0.42f;

        glyphOutlineWidth = size * 0.13f;
        glyphMainWidth = size * 0.102f;
        glyphInnerWidth = size * 0.044f;

        glyphRect.set(
                tileRect.left + tileRect.width() * 0.21f,
                tileRect.top + tileRect.height() * 0.18f,
                tileRect.right - tileRect.width() * 0.15f,
                tileRect.bottom - tileRect.height() * 0.18f
        );

        tilePath.reset();
        tilePath.addRoundRect(tileRect, tileRadius, tileRadius, Path.Direction.CW);

        buildGlyphPath();
        updateShaders();
    }

    private void buildGlyphPath() {
        float left = glyphRect.left;
        float top = glyphRect.top;
        float width = glyphRect.width();
        float height = glyphRect.height();

        glyphPath.reset();
        glyphPath.moveTo(left + width * 0.16f, top + height * 0.06f);
        glyphPath.lineTo(left + width * 0.16f, top + height * 0.84f);
        glyphPath.quadTo(
                left + width * 0.18f, top + height * 0.56f,
                left + width * 0.47f, top + height * 0.41f
        );
        glyphPath.quadTo(
                left + width * 0.68f, top + height * 0.31f,
                left + width * 0.81f, top + height * 0.09f
        );
        glyphPath.lineTo(left + width * 0.72f, top + height * 0.84f);
        glyphPath.lineTo(left + width * 0.93f, top + height * 0.84f);
    }

    private void updateShaders() {
        tilePaint.setShader(new LinearGradient(
                tileRect.left,
                tileRect.top,
                tileRect.right,
                tileRect.bottom,
                new int[]{
                        Color.parseColor("#FFFFFF"),
                        Color.parseColor("#F3FBFE"),
                        Color.parseColor("#E6F3FA"),
                        Color.parseColor("#D5EAF4")
                },
                new float[]{0f, 0.34f, 0.7f, 1f},
                Shader.TileMode.CLAMP
        ));

        tileGlossPaint.setShader(new LinearGradient(
                tileRect.left - tileRect.width() * 0.18f,
                tileRect.top,
                tileRect.right,
                tileRect.bottom + tileRect.height() * 0.22f,
                new int[]{
                        Color.argb(0, 255, 255, 255),
                        Color.argb(110, 255, 255, 255),
                        Color.argb(168, 255, 255, 255),
                        Color.argb(36, 224, 246, 255),
                        Color.argb(0, 224, 246, 255)
                },
                new float[]{0f, 0.18f, 0.38f, 0.62f, 1f},
                Shader.TileMode.CLAMP
        ));

        ambientGlowPaint.setShader(new RadialGradient(
                centerX,
                centerY,
                glowRadius,
                new int[]{
                        Color.argb(104, 183, 226, 255),
                        Color.argb(40, 140, 208, 235),
                        Color.argb(0, 140, 208, 235)
                },
                new float[]{0f, 0.52f, 1f},
                Shader.TileMode.CLAMP
        ));

        glyphOutlinePaint.setStrokeWidth(glyphOutlineWidth);

        glyphMainPaint.setStrokeWidth(glyphMainWidth);
        glyphMainPaint.setShader(new LinearGradient(
                glyphRect.left,
                glyphRect.top,
                glyphRect.right,
                glyphRect.bottom,
                new int[]{
                        Color.parseColor("#2E86C9"),
                        Color.parseColor("#2792DC"),
                        Color.parseColor("#27B3CA"),
                        Color.parseColor("#7AD0EC")
                },
                new float[]{0f, 0.32f, 0.7f, 1f},
                Shader.TileMode.CLAMP
        ));

        glyphInnerPaint.setStrokeWidth(glyphInnerWidth);
        glyphInnerPaint.setColor(Color.argb(188, 248, 252, 255));

        glyphShimmerPaint.setStrokeWidth(glyphMainWidth * 0.42f);
        glyphShimmerPaint.setShader(new LinearGradient(
                glyphRect.left,
                glyphRect.top,
                glyphRect.right,
                glyphRect.bottom,
                new int[]{
                        Color.argb(0, 255, 255, 255),
                        Color.argb(214, 255, 255, 255),
                        Color.argb(0, 255, 255, 255)
                },
                new float[]{0.2f, 0.5f, 0.82f},
                Shader.TileMode.CLAMP
        ));
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        if (tileRect.isEmpty()) {
            return;
        }

        ambientGlowPaint.setAlpha(Math.round(72 + 36 * pulseProgress));
        canvas.drawCircle(centerX, centerY, glowRadius * (0.94f + pulseProgress * 0.08f), ambientGlowPaint);

        canvas.drawRoundRect(
                tileRect.left + dp(2f),
                tileRect.top + dp(9f),
                tileRect.right - dp(2f),
                tileRect.bottom + dp(8f),
                tileRadius,
                tileRadius,
                tileShadowPaint
        );

        canvas.drawRoundRect(tileRect, tileRadius, tileRadius, tilePaint);
        drawTileGloss(canvas);
        canvas.drawRoundRect(tileRect, tileRadius, tileRadius, tileStrokePaint);

        drawGlyph(canvas, glyphPath);
        drawFlare(canvas);
    }

    private void drawTileGloss(Canvas canvas) {
        shaderMatrix.reset();
        float baseShift = tileRect.width() * (0.68f * shimmerProgress - 0.16f);
        float rotationShift = ringRotation * tileRect.width() * 0.05f;
        shaderMatrix.setTranslate(baseShift + rotationShift, 0f);
        Shader shader = tileGlossPaint.getShader();
        if (shader != null) {
            shader.setLocalMatrix(shaderMatrix);
        }
        tileGlossPaint.setAlpha(Math.round(132 + 36 * pulseProgress));
        canvas.drawRoundRect(tileRect, tileRadius, tileRadius, tileGlossPaint);
    }

    private void drawGlyph(Canvas canvas, Path path) {
        shaderMatrix.reset();
        float shimmerShift = glyphRect.width() * (0.78f * shimmerProgress - 0.18f);
        shaderMatrix.setTranslate(shimmerShift, 0f);
        Shader shimmerShader = glyphShimmerPaint.getShader();
        if (shimmerShader != null) {
            shimmerShader.setLocalMatrix(shaderMatrix);
        }

        canvas.drawPath(path, glyphOutlinePaint);
        canvas.drawPath(path, glyphMainPaint);
        canvas.drawPath(path, glyphInnerPaint);
        canvas.drawPath(path, glyphShimmerPaint);
    }

    private void drawFlare(Canvas canvas) {
        float flareRadius = tileRect.width() * (0.06f + pulseProgress * 0.014f);
        float flareX = tileRect.left + tileRect.width() * (0.62f + shimmerProgress * 0.08f);
        float flareY = tileRect.top + tileRect.height() * 0.22f;
        flarePaint.setShader(new RadialGradient(
                flareX,
                flareY,
                flareRadius,
                new int[]{
                        Color.argb(190, 255, 255, 255),
                        Color.argb(64, 180, 226, 255),
                        Color.argb(0, 180, 226, 255)
                },
                new float[]{0f, 0.44f, 1f},
                Shader.TileMode.CLAMP
        ));
        canvas.drawCircle(flareX, flareY, flareRadius, flarePaint);
    }

    public void setShimmerProgress(float shimmerProgress) {
        this.shimmerProgress = shimmerProgress;
        invalidate();
    }

    public float getShimmerProgress() {
        return shimmerProgress;
    }

    public void setPulseProgress(float pulseProgress) {
        this.pulseProgress = pulseProgress;
        invalidate();
    }

    public float getPulseProgress() {
        return pulseProgress;
    }

    public void setRingRotation(float ringRotation) {
        this.ringRotation = ringRotation;
        invalidate();
    }

    public float getRingRotation() {
        return ringRotation;
    }

    private float dp(float value) {
        return TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value, getResources().getDisplayMetrics());
    }
}

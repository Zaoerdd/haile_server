package com.example.haile;

import android.animation.AnimatorSet;
import android.animation.ObjectAnimator;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.animation.AccelerateDecelerateInterpolator;
import android.view.animation.OvershootInterpolator;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

public class SplashActivity extends AppCompatActivity {

    private static final long SPLASH_DURATION_MS = 1850L;
    private static final long SKIP_APPEAR_DELAY_MS = 460L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean navigationCommitted = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_splash);

        View glowView = findViewById(R.id.splash_glow);
        BrandMarkView markView = findViewById(R.id.splash_mark);
        TextView statusView = findViewById(R.id.splash_status);
        TextView skipView = findViewById(R.id.splash_skip);

        skipView.setAlpha(0f);
        skipView.setOnClickListener(v -> openMain());
        skipView.animate().alpha(1f).setStartDelay(SKIP_APPEAR_DELAY_MS).setDuration(180L).start();

        startIntroAnimation(glowView, markView, statusView);
        handler.postDelayed(this::openMain, SPLASH_DURATION_MS);
    }

    private void startIntroAnimation(View glowView, BrandMarkView markView, TextView statusView) {
        glowView.setAlpha(0f);
        glowView.setScaleX(0.74f);
        glowView.setScaleY(0.74f);

        markView.setAlpha(0f);
        markView.setScaleX(0.84f);
        markView.setScaleY(0.84f);
        markView.setTranslationY(54f);
        markView.setShimmerProgress(0.08f);
        markView.setPulseProgress(0f);
        markView.setRingRotation(-0.35f);

        statusView.setAlpha(0f);
        statusView.setTranslationY(20f);

        ObjectAnimator glowAlpha = ObjectAnimator.ofFloat(glowView, View.ALPHA, 0f, 0.74f, 0.22f);
        ObjectAnimator glowScaleX = ObjectAnimator.ofFloat(glowView, View.SCALE_X, 0.74f, 1.08f);
        ObjectAnimator glowScaleY = ObjectAnimator.ofFloat(glowView, View.SCALE_Y, 0.74f, 1.08f);

        ObjectAnimator markAlpha = ObjectAnimator.ofFloat(markView, View.ALPHA, 0f, 1f);
        ObjectAnimator markScaleX = ObjectAnimator.ofFloat(markView, View.SCALE_X, 0.84f, 1.03f, 1f);
        ObjectAnimator markScaleY = ObjectAnimator.ofFloat(markView, View.SCALE_Y, 0.84f, 1.03f, 1f);
        ObjectAnimator markTranslateY = ObjectAnimator.ofFloat(markView, View.TRANSLATION_Y, 54f, 0f);
        ObjectAnimator markShimmer = ObjectAnimator.ofFloat(markView, "shimmerProgress", 0.08f, 1f);
        ObjectAnimator markPulse = ObjectAnimator.ofFloat(markView, "pulseProgress", 0f, 1f, 0.38f);
        ObjectAnimator ringRotation = ObjectAnimator.ofFloat(markView, "ringRotation", -0.35f, 0.4f);

        ObjectAnimator statusAlpha = ObjectAnimator.ofFloat(statusView, View.ALPHA, 0f, 1f);
        ObjectAnimator statusTranslateY = ObjectAnimator.ofFloat(statusView, View.TRANSLATION_Y, 20f, 0f);
        statusAlpha.setStartDelay(210L);
        statusTranslateY.setStartDelay(210L);

        AnimatorSet introSet = new AnimatorSet();
        introSet.playTogether(
                glowAlpha,
                glowScaleX,
                glowScaleY,
                markAlpha,
                markScaleX,
                markScaleY,
                markTranslateY,
                markShimmer,
                markPulse,
                ringRotation,
                statusAlpha,
                statusTranslateY
        );
        introSet.setDuration(980L);
        introSet.setInterpolator(new AccelerateDecelerateInterpolator());
        introSet.start();

        markView.animate()
                .rotationBy(0.25f)
                .setDuration(720L)
                .setInterpolator(new OvershootInterpolator(0.38f))
                .start();
    }

    private void openMain() {
        if (navigationCommitted) {
            return;
        }
        navigationCommitted = true;
        handler.removeCallbacksAndMessages(null);
        startActivity(new Intent(this, MainActivity.class));
        overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out);
        finish();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }
}

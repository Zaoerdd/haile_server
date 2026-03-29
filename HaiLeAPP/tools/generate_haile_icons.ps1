param(
    [string]$ResRoot = (Join-Path $PSScriptRoot '..\app\src\main\res')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing

function New-Color {
    param(
        [string]$Hex,
        [int]$Alpha = 255
    )

    $value = $Hex.TrimStart('#')
    [System.Drawing.Color]::FromArgb(
        $Alpha,
        [Convert]::ToInt32($value.Substring(0, 2), 16),
        [Convert]::ToInt32($value.Substring(2, 2), 16),
        [Convert]::ToInt32($value.Substring(4, 2), 16)
    )
}

function New-RoundedRectPath {
    param(
        [System.Drawing.RectangleF]$Rect,
        [float]$Radius
    )

    $diameter = $Radius * 2
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddArc($Rect.X, $Rect.Y, $diameter, $diameter, 180, 90)
    $path.AddArc($Rect.Right - $diameter, $Rect.Y, $diameter, $diameter, 270, 90)
    $path.AddArc($Rect.Right - $diameter, $Rect.Bottom - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($Rect.X, $Rect.Bottom - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    $path
}

function New-GlyphPath {
    param([System.Drawing.RectangleF]$GlyphRect)

    $left = $GlyphRect.Left
    $top = $GlyphRect.Top
    $width = $GlyphRect.Width
    $height = $GlyphRect.Height

    $glyph = New-Object System.Drawing.Drawing2D.GraphicsPath
    $glyph.StartFigure()
    $glyph.AddLine(
        $left + $width * 0.16, $top + $height * 0.06,
        $left + $width * 0.16, $top + $height * 0.84
    )
    $glyph.AddBezier(
        $left + $width * 0.16, $top + $height * 0.84,
        $left + $width * 0.18, $top + $height * 0.56,
        $left + $width * 0.29, $top + $height * 0.46,
        $left + $width * 0.47, $top + $height * 0.41
    )
    $glyph.AddBezier(
        $left + $width * 0.47, $top + $height * 0.41,
        $left + $width * 0.68, $top + $height * 0.31,
        $left + $width * 0.76, $top + $height * 0.22,
        $left + $width * 0.81, $top + $height * 0.09
    )
    $glyph.AddLine(
        $left + $width * 0.72, $top + $height * 0.84,
        $left + $width * 0.93, $top + $height * 0.84
    )
    $glyph
}

function Draw-GlyphSet {
    param(
        [System.Drawing.Graphics]$Graphics,
        [System.Drawing.RectangleF]$GlyphRect,
        [float]$Size
    )

    $glyphBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($GlyphRect, (New-Color '#2E86C9'), (New-Color '#7AD0EC'), 45.0)
    $glyphBlend = New-Object System.Drawing.Drawing2D.ColorBlend
    $glyphBlend.Colors = @(
        (New-Color '#2E86C9'),
        (New-Color '#2792DC'),
        (New-Color '#27B3CA'),
        (New-Color '#7AD0EC')
    )
    $glyphBlend.Positions = @(0.0, 0.32, 0.7, 1.0)
    $glyphBrush.InterpolationColors = $glyphBlend

    $glyphPath = New-GlyphPath $GlyphRect

    $outlinePen = New-Object System.Drawing.Pen((New-Color '#FAFDFF' 236), [float]($Size * 0.115))
    $outlinePen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $outlinePen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $outlinePen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $Graphics.DrawPath($outlinePen, $glyphPath)

    $mainPen = New-Object System.Drawing.Pen($glyphBrush, [float]($Size * 0.09))
    $mainPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $mainPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $mainPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $Graphics.DrawPath($mainPen, $glyphPath)

    $innerPen = New-Object System.Drawing.Pen((New-Color '#F8FCFF' 184), [float]($Size * 0.038))
    $innerPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $innerPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $innerPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $Graphics.DrawPath($innerPen, $glyphPath)
}

function Draw-BrandIcon {
    param(
        [System.Drawing.Graphics]$Graphics,
        [int]$Size,
        [bool]$RoundVariant
    )

    $Graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $Graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
    $Graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $Graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $Graphics.Clear([System.Drawing.Color]::Transparent)

    if ($RoundVariant) {
        $surfaceRect = [System.Drawing.RectangleF]::new($Size * 0.10, $Size * 0.10, $Size * 0.80, $Size * 0.80)
        $surfaceBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($surfaceRect, (New-Color '#FFFFFF'), (New-Color '#D7EAF4'), 45.0)
        $Graphics.FillEllipse($surfaceBrush, $surfaceRect)

        $rimPen = New-Object System.Drawing.Pen((New-Color '#C9E0EE' 210), [float]($Size * 0.018))
        $Graphics.DrawEllipse($rimPen, $surfaceRect)

        $glowBrush = New-Object System.Drawing.Drawing2D.PathGradientBrush((New-RoundedRectPath $surfaceRect ($surfaceRect.Width / 2)))
        $glowBrush.CenterPoint = [System.Drawing.PointF]::new($surfaceRect.Left + ($surfaceRect.Width * 0.36), $surfaceRect.Top + ($surfaceRect.Height * 0.26))
        $glowBrush.CenterColor = (New-Color '#FFFFFF' 218)
        $glowBrush.SurroundColors = @((New-Color '#FFFFFF' 0))
        $Graphics.FillEllipse($glowBrush, $surfaceRect)

        $glyphRect = [System.Drawing.RectangleF]::new(
            $surfaceRect.Left + ($surfaceRect.Width * 0.20),
            $surfaceRect.Top + ($surfaceRect.Height * 0.25),
            $surfaceRect.Width * 0.60,
            $surfaceRect.Height * 0.60
        )
        Draw-GlyphSet -Graphics $Graphics -GlyphRect $glyphRect -Size $Size
        return
    }

    $tileInset = $Size * 0.08
    $tileRect = [System.Drawing.RectangleF]::new($tileInset, $tileInset, $Size - ($tileInset * 2), $Size - ($tileInset * 2))
    $tileRadius = $Size * 0.17
    $tilePath = New-RoundedRectPath $tileRect $tileRadius

    $surfaceBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($tileRect, (New-Color '#FFFFFF'), (New-Color '#D5EAF4'), 45.0)
    $surfaceBlend = New-Object System.Drawing.Drawing2D.ColorBlend
    $surfaceBlend.Colors = @(
        (New-Color '#FFFFFF'),
        (New-Color '#F4FBFE'),
        (New-Color '#E6F3FA'),
        (New-Color '#D5EAF4')
    )
    $surfaceBlend.Positions = @(0.0, 0.34, 0.7, 1.0)
    $surfaceBrush.InterpolationColors = $surfaceBlend
    $Graphics.FillPath($surfaceBrush, $tilePath)

    $glossBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($tileRect, (New-Color '#FFFFFF' 0), (New-Color '#FFFFFF' 0), 45.0)
    $glossBlend = New-Object System.Drawing.Drawing2D.ColorBlend
    $glossBlend.Colors = @(
        (New-Color '#FFFFFF' 0),
        (New-Color '#FFFFFF' 108),
        (New-Color '#FFFFFF' 166),
        (New-Color '#E0F6FF' 34),
        (New-Color '#E0F6FF' 0)
    )
    $glossBlend.Positions = @(0.0, 0.18, 0.38, 0.62, 1.0)
    $glossBrush.InterpolationColors = $glossBlend
    $Graphics.FillPath($glossBrush, $tilePath)

    $borderPen = New-Object System.Drawing.Pen((New-Color '#C5DEEE' 216), [float]($Size * 0.012))
    $Graphics.DrawPath($borderPen, $tilePath)

    $glyphRect = [System.Drawing.RectangleF]::new(
        $tileRect.Left + ($tileRect.Width * 0.22),
        $tileRect.Top + ($tileRect.Height * 0.27),
        $tileRect.Width * 0.58,
        $tileRect.Height * 0.58
    )
    Draw-GlyphSet -Graphics $Graphics -GlyphRect $glyphRect -Size $Size
}

function Save-BrandIcon {
    param(
        [int]$Size,
        [bool]$RoundVariant,
        [string]$OutputPath
    )

    $bitmap = New-Object System.Drawing.Bitmap($Size, $Size)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)

    try {
        Draw-BrandIcon -Graphics $graphics -Size $Size -RoundVariant $RoundVariant
        $directory = Split-Path -Parent $OutputPath
        if (-not (Test-Path $directory)) {
            New-Item -ItemType Directory -Force -Path $directory | Out-Null
        }
        $bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

$sizes = @{
    'mipmap-mdpi' = 48
    'mipmap-hdpi' = 72
    'mipmap-xhdpi' = 96
    'mipmap-xxhdpi' = 144
    'mipmap-xxxhdpi' = 192
}

foreach ($density in $sizes.Keys) {
    $size = $sizes[$density]
    Save-BrandIcon -Size $size -RoundVariant:$false -OutputPath (Join-Path $ResRoot "$density\haile_launcher.png")
    Save-BrandIcon -Size $size -RoundVariant:$true -OutputPath (Join-Path $ResRoot "$density\haile_launcher_round.png")
}

Save-BrandIcon -Size 1024 -RoundVariant:$false -OutputPath (Join-Path $ResRoot 'drawable-nodpi\haile_launcher_master.png')

package dev.alpine.codexclient.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

internal val AlpineInk = Color(0xFF10120F)
internal val AlpinePaper = Color(0xFFF4F3ED)
internal val AlpineAcid = Color(0xFFB9F227)
internal val AlpineSlate = Color(0xFF31372F)
internal val AlpineRaisedSurface = Color(0xFFFAFAF7)
internal val AlpineHighSurface = Color(0xFFFCFCFA)
internal val AlpineLocal = Color(0xFFE7EEE2)
internal val AlpineWarning = Color(0xFFFFE7A6)
internal val AlpineError = Color(0xFFFFD7D2)
internal val AlpineErrorInk = Color(0xFF9A1B12)
internal val AlpineInfo = Color(0xFFDDE7FF)
internal val AlpineCodex = Color(0xFF202420)
internal val AlpineGrok = Color(0xFF2758F2)
internal val AlpineOutline = Color(0x2410120F)
internal val AlpineStrongOutline = Color(0x3D10120F)

private val AlpineLightColorScheme = lightColorScheme(
    primary = AlpineInk,
    onPrimary = AlpinePaper,
    primaryContainer = AlpineAcid,
    onPrimaryContainer = AlpineInk,
    secondary = AlpineAcid,
    onSecondary = AlpineInk,
    secondaryContainer = AlpineLocal,
    onSecondaryContainer = AlpineInk,
    tertiary = AlpineGrok,
    onTertiary = Color.White,
    background = AlpinePaper,
    onBackground = AlpineInk,
    surface = AlpinePaper,
    onSurface = AlpineInk,
    surfaceVariant = AlpineRaisedSurface,
    onSurfaceVariant = AlpineInk,
    outline = AlpineStrongOutline,
    outlineVariant = AlpineOutline,
    error = AlpineErrorInk,
    onError = Color.White,
    errorContainer = AlpineError,
    onErrorContainer = AlpineErrorInk,
    inverseSurface = AlpineInk,
    inverseOnSurface = AlpinePaper,
    inversePrimary = AlpineAcid,
    surfaceTint = Color.Transparent,
)

private val AlpineTypography = Typography(
    headlineSmall = TextStyle(
        fontWeight = FontWeight.Black,
        fontSize = 30.sp,
        lineHeight = 34.sp,
        letterSpacing = (-0.6).sp,
    ),
    titleLarge = TextStyle(
        fontWeight = FontWeight.ExtraBold,
        fontSize = 22.sp,
        lineHeight = 28.sp,
    ),
    titleMedium = TextStyle(
        fontWeight = FontWeight.Bold,
        fontSize = 16.sp,
        lineHeight = 22.sp,
    ),
    bodyLarge = TextStyle(
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 24.sp,
    ),
    bodyMedium = TextStyle(
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 21.sp,
    ),
    labelLarge = TextStyle(
        fontWeight = FontWeight.ExtraBold,
        fontSize = 12.sp,
        lineHeight = 16.sp,
    ),
    labelMedium = TextStyle(
        fontWeight = FontWeight.Bold,
        fontSize = 11.sp,
        lineHeight = 16.sp,
    ),
    labelSmall = TextStyle(
        fontWeight = FontWeight.Bold,
        fontSize = 10.sp,
        lineHeight = 14.sp,
    ),
)

private val AlpineShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(14.dp),
    large = RoundedCornerShape(18.dp),
    extraLarge = RoundedCornerShape(24.dp),
)

@Composable
internal fun AlpineAgentTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = AlpineLightColorScheme,
        typography = AlpineTypography,
        shapes = AlpineShapes,
        content = content,
    )
}

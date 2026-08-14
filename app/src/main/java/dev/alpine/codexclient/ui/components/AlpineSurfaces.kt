package dev.alpine.codexclient.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.alpine.codexclient.ui.theme.AlpineAcid
import dev.alpine.codexclient.ui.theme.AlpineInk
import dev.alpine.codexclient.ui.theme.AlpineOutline
import dev.alpine.codexclient.ui.theme.AlpineRaisedSurface

@Composable
internal fun AlpineStatusBadge(
    label: String,
    modifier: Modifier = Modifier,
    containerColor: Color = AlpineAcid,
    contentColor: Color = AlpineInk,
) {
    Surface(
        modifier = modifier,
        color = containerColor,
        contentColor = contentColor,
        shape = MaterialTheme.shapes.extraSmall,
    ) {
        Text(
            text = label,
            modifier = Modifier.padding(horizontal = 9.dp, vertical = 5.dp),
            style = MaterialTheme.typography.labelSmall.copy(
                fontWeight = FontWeight.Black,
                letterSpacing = 1.sp,
            ),
        )
    }
}

@Composable
internal fun AlpinePanel(
    modifier: Modifier = Modifier,
    containerColor: Color = AlpineRaisedSurface,
    contentColor: Color = AlpineInk,
    borderColor: Color = AlpineOutline,
    padding: PaddingValues = PaddingValues(16.dp),
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier,
        color = containerColor,
        contentColor = contentColor,
        shape = MaterialTheme.shapes.large,
        border = BorderStroke(1.dp, borderColor),
        tonalElevation = 0.dp,
        shadowElevation = 0.dp,
    ) {
        Column(modifier = Modifier.padding(padding), content = content)
    }
}

@Composable
internal fun AlpineSectionHeader(
    eyebrow: String,
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
) {
    Column(modifier = modifier) {
        Text(
            text = eyebrow,
            style = MaterialTheme.typography.labelSmall.copy(
                fontWeight = FontWeight.Black,
                letterSpacing = 1.1.sp,
            ),
        )
        Text(
            text = title,
            modifier = Modifier.padding(top = 4.dp),
            style = MaterialTheme.typography.titleLarge,
        )
        subtitle?.let {
            Text(
                text = it,
                modifier = Modifier.padding(top = 6.dp),
                color = AlpineInk.copy(alpha = 0.68f),
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

---
name: Institutional Intelligence
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#45464d'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#76777d'
  outline-variant: '#c6c6cd'
  surface-tint: '#565e74'
  primary: '#000000'
  on-primary: '#ffffff'
  primary-container: '#131b2e'
  on-primary-container: '#7c839b'
  inverse-primary: '#bec6e0'
  secondary: '#0058be'
  on-secondary: '#ffffff'
  secondary-container: '#2170e4'
  on-secondary-container: '#fefcff'
  tertiary: '#000000'
  on-tertiary: '#ffffff'
  tertiary-container: '#271901'
  on-tertiary-container: '#98805d'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dae2fd'
  primary-fixed-dim: '#bec6e0'
  on-primary-fixed: '#131b2e'
  on-primary-fixed-variant: '#3f465c'
  secondary-fixed: '#d8e2ff'
  secondary-fixed-dim: '#adc6ff'
  on-secondary-fixed: '#001a42'
  on-secondary-fixed-variant: '#004395'
  tertiary-fixed: '#fcdeb5'
  tertiary-fixed-dim: '#dec29a'
  on-tertiary-fixed: '#271901'
  on-tertiary-fixed-variant: '#574425'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  data-tabular:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
  label-mono:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  container-margin: 24px
  gutter: 16px
  cell-padding-x: 12px
  cell-padding-y: 8px
---

## Brand & Style
The design system is engineered for high-stakes financial environments where data density and clarity are paramount. The brand personality is authoritative, precise, and systematic, designed to instill immediate trust in institutional users managing significant capital.

The style is **Corporate / Modern** with a focus on **Minimalism** to reduce cognitive load. It prioritizes a high information-to-ink ratio, ensuring that complex metrics like Altman Z-Scores and Piotroski F-Scores are the primary focus. Surfaces are clean, layout transitions are snappy, and the visual language avoids unnecessary decoration in favor of functional clarity.

## Colors
The color strategy utilizes a "Deep Navy" (`#0F172A`) for primary branding, sidebar navigation, and header backgrounds to establish a professional anchor. The main workspace uses a light grey-to-white palette to ensure maximum legibility for data tables.

Semantic colors are strictly reserved for financial performance indicators:
- **Success Green (#10B981):** Positive gains, "Pass" scores, or low-risk indicators.
- **Danger Red (#EF4444):** Market losses, "Fail" scores, or high-risk warnings.
- **Info Blue (#3B82F6):** Primary actions, interactive links, and neutral data highlights.
- **Warning Amber (#F59E0B):** Used sparingly for moderate-risk signals.

## Typography
This design system utilizes **Inter** as its primary typeface due to its exceptional legibility at small sizes and high-quality "tabular lining" features. All financial figures must use `font-variant-numeric: tabular-nums` to ensure that numbers align vertically in tables, allowing for instant comparison of values.

**JetBrains Mono** is introduced as a secondary label font for technical metadata and identifiers (e.g., Tickers, ISINs, or Score Codes), reinforcing the technical, precise nature of the platform.

Mobile adjustments: `display-lg` should scale down to 24px (`headline-md`) on devices smaller than 768px.

## Layout & Spacing
The layout follows a **Fixed Grid** philosophy for the main content area (max-width: 1440px) to maintain a controlled reading environment for dense data. A 12-column system is used for dashboard widgets, allowing for common configurations of 1/4, 1/3, 1/2, and full-width modules.

Spacing is based on a strict **4px baseline**. Dashboard margins are set to 24px. For data-dense tables, use compact vertical padding (8px) and standard horizontal padding (12px) to maximize the number of rows visible above the fold. 

**Breakpoints:**
- **Desktop:** 1200px+ (12 columns, 24px margins)
- **Tablet:** 768px - 1199px (8 columns, 16px margins)
- **Mobile:** <768px (4 columns, 12px margins, stacked widgets)

## Elevation & Depth
The design system employs **Tonal Layers** rather than heavy shadows to signify depth. This keeps the UI feeling flat and "fast."

- **Base Layer:** Background color (`#F8FAFC`) for the main workspace.
- **Surface Layer:** White (`#FFFFFF`) cards for dashboard widgets and tables.
- **Stroke:** A subtle 1px border (`#E2E8F0`) is used instead of shadows to define card boundaries.
- **Active State:** A soft, 4px blur shadow with low opacity (10%) is only used on "Hover" or "Active" states of interactive cards to provide subtle feedback.
- **Overlays:** Modals use a 20% opacity Deep Navy backdrop with a 16px blur.

## Shapes
A **Soft** shape language is applied to prevent the UI from feeling overly aggressive. 
- Elements like buttons and input fields use a `0.25rem` (4px) radius.
- Large containers and data cards use a `rounded-lg` (8px) radius.
- "Health/Risk Gauges" utilize a circular or semi-circular arc, but their containers remain strictly rectangular to maintain grid alignment.

## Components

### Data Tables
The core of the system. Headers must be "Sticky." Column headers use `label-mono` and support integrated sorting/filtering icons. Row hover states should use a subtle tint of `#F1F5F9`.

### Sparklines
Integrated into table rows or small cards. Use a 1.5px stroke width. The color is determined by the net change of the period: `success_color` for growth, `danger_color` for decline. No area fill.

### Health/Risk Gauges
Used for Altman Z-Score and Piotroski F-Score. These are 180-degree gauge arcs with a needle indicator. The track is divided into semantic segments (e.g., Red/Yellow/Green) to provide instant context for the numeric value.

### Buttons
- **Primary:** Deep Navy background with White text.
- **Secondary:** White background with 1px Stroke (`#E2E8F0`) and Navy text.
- **Tertiary/Ghost:** No background, Blue text. Used for "View All" or "Export" actions.

### Input Fields
Standardized height of 36px for a compact, professional feel. Uses a 1px border that shifts to `primary_color` on focus. Placeholder text is in `neutral_color`.

### Status Chips
Small, low-contrast pills. For example, a "Low Risk" chip uses a 10% opacity Green background with a 100% opacity Green text. No border.
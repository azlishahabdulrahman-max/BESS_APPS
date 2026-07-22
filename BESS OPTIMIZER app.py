import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Set page layout
st.set_page_config(
    page_title="C&I BESS Sizing & Peak Shaving Simulator",
    page_icon="⚡",
    layout="wide"
)

# Custom Styling
st.markdown("""
<style>
    .main {
        background-color: #f8f9fa;
    }
    .stMetric {
        background-color: #ffffff;
        padding: 15px;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
</style>
""", unsafe_allow_html=True)

st.title("⚡ C&I BESS Sizing & Peak Shaving Visualizer")
st.markdown("Analisis prestasi sistem storan tenaga bateri (BESS) untuk pengurangan *Maximum Demand* (MD) dan peralihan beban (*load shifting*).")

# Sidebar - Parameters
st.sidebar.header("⚙️ Dynamic System Parameters")

# Building & Load Profile Defaults
st.sidebar.subheader("1. Profile Settings")
base_peak_load = st.sidebar.number_input("Peak Building Load (kW)", min_value=100, max_value=5000, value=1200, step=50)
base_baseload = st.sidebar.number_input("Baseload (kW)", min_value=50, max_value=2000, value=300, step=25)

# BESS Specifications
st.sidebar.subheader("2. BESS Specifications")
bess_power_kw = st.sidebar.slider("BESS Power Rating (kW)", min_value=50, max_value=1000, value=350, step=25)
bess_capacity_kwh = st.sidebar.slider("BESS Energy Capacity (kWh)", min_value=100, max_value=3000, value=1000, step=50)
soc_min_pct = st.sidebar.slider("Min State of Charge (SoC %)", min_value=10, max_value=30, value=20)
soc_max_pct = st.sidebar.slider("Max State of Charge (SoC %)", min_value=80, max_value=100, value=100)
bess_rte = st.sidebar.slider("Round-Trip Efficiency (%)", min_value=75, max_value=98, value=90) / 100.0

# Commercial Tariff Settings (e.g. C&I Time-of-Use)
st.sidebar.subheader("3. Tariff Settings")
md_charge_rate = st.sidebar.number_input("MD Charge Rate ($/kW/month)", value=12.50)
peak_tariff = st.sidebar.number_input("Peak Energy Tariff ($/kWh)", value=0.18)
offpeak_tariff = st.sidebar.number_input("Off-Peak Energy Tariff ($/kWh)", value=0.09)

# Generation of Synthetic 48-Hour High-Resolution Load & BESS Simulation Data
@st.cache_data
def run_bess_simulation(peak_load, baseload, p_bess, e_bess, min_soc, max_soc, rte):
    hours = 48
    time = np.linspace(0, hours, hours * 4) # 15-min intervals
    dt = 0.25 # 15 mins
    
    # Synthetic C&I load profile with morning/afternoon peaks
    load = baseload + (peak_load - baseload) * (
        0.4 * np.exp(-((time % 24 - 11)**2) / 8) +
        0.95 * np.exp(-((time % 24 - 15.5)**2) / 12) +
        0.1 * np.random.normal(0, 0.2, len(time))
    )
    load = np.maximum(load, baseload)
    
    # Target Peak Threshold (Target Shaving)
    target_shave_threshold = peak_load - (p_bess * 0.85)
    
    # Simulation variables
    grid_import = np.zeros_like(load)
    bess_p = np.zeros_like(load) # Positive = Discharge, Negative = Charge
    soc = np.zeros_like(load)
    
    curr_energy = e_bess * 0.5 # Start at 50% SoC
    eta_ch = np.sqrt(rte)
    eta_dis = np.sqrt(rte)
    
    e_min = (min_soc / 100.0) * e_bess
    e_max = (max_soc / 100.0) * e_bess
    
    for i, t in enumerate(time):
        tod = t % 24 # Time of day
        dem = load[i]
        
        p_ch = 0.0
        p_dis = 0.0
        
        # Dispatch Logic: Discharging during Peak Hours (08:00 - 22:00) when load > threshold
        if 8.0 <= tod <= 22.0:
            if dem > target_shave_threshold:
                req_power = dem - target_shave_threshold
                avail_power_energy = (curr_energy - e_min) * eta_dis / dt
                p_dis = min(req_power, p_bess, max(0.0, avail_power_energy))
        
        # Dispatch Logic: Charging during Off-Peak Hours (00:00 - 06:00)
        elif 0.0 <= tod <= 6.0:
            if curr_energy < e_max:
                headroom_power = (e_max - curr_energy) / (eta_ch * dt)
                p_ch = min(p_bess * 0.6, max(0.0, headroom_power))
                
        # Net BESS Power
        p_net = p_dis - p_ch # Positive: discharging, Negative: charging
        
        # Energy Update
        if p_net > 0:
            curr_energy -= (p_net / eta_dis) * dt
        else:
            curr_energy += (-p_net * eta_ch) * dt
            
        curr_energy = np.clip(curr_energy, e_min, e_max)
        
        bess_p[i] = p_net
        grid_import[i] = dem - p_net
        soc[i] = (curr_energy / e_bess) * 100.0
        
    df_res = pd.DataFrame({
        "Time_Hr": time,
        "Load_kW": load,
        "Grid_kW": grid_import,
        "BESS_Power_kW": bess_p,
        "SoC_Pct": soc
    })
    return df_res

# Run simulation
df_sim = run_bess_simulation(base_peak_load, base_baseload, bess_power_kw, bess_capacity_kwh, soc_min_pct, soc_max_pct, bess_rte)

# Key Metrics Calculation
orig_max_demand = df_sim["Load_kW"].max()
new_max_demand = df_sim["Grid_kW"].max()
md_reduction = orig_max_demand - new_max_demand
monthly_md_savings = md_reduction * md_charge_rate

# Visual KPI Header
col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
col_kpi1.metric("Original Peak Demand", f"{orig_max_demand:.1f} kW")
col_kpi2.metric("New Grid Peak Demand", f"{new_max_demand:.1f} kW", delta=f"-{md_reduction:.1f} kW", delta_color="normal")
col_kpi3.metric("Peak Demand Shaved", f"{(md_reduction/orig_max_demand)*100:.1f} %")
col_kpi4.metric("Est. Monthly MD Savings", f"${monthly_md_savings:,.2f}")

st.markdown("---")

# Main Interactive Plotly Chart
st.subheader("📈 Interactive Power & BESS State-of-Charge (SoC) Profiles")

# Create Subplots: Top for Power Profiles, Bottom for SoC
fig = make_subplots(
    rows=2, cols=1,
    shared_xaxes=True,
    vertical_spacing=0.08,
    subplot_titles=("Power Profile & Peak Shaving (kW)", "BESS State of Charge (% SoC)"),
    row_heights=[0.65, 0.35]
)

# Row 1: Building Load, Grid Import, & Peak Threshold Line
fig.add_trace(
    go.Scatter(
        x=df_sim["Time_Hr"], y=df_sim["Load_kW"],
        mode="lines", name="Original Building Load",
        line=dict(color="#eb4d4b", width=2, dash="dash")
    ),
    row=1, col=1
)

fig.add_trace(
    go.Scatter(
        x=df_sim["Time_Hr"], y=df_sim["Grid_kW"],
        mode="lines", name="New Grid Demand (Post-BESS)",
        line=dict(color="#10ac84", width=2.5),
        fill="tozeroy", fillcolor="rgba(16, 172, 132, 0.1)"
    ),
    row=1, col=1
)

# Peak Shaving Shaded Highlight Area
fig.add_trace(
    go.Scatter(
        x=df_sim["Time_Hr"], y=df_sim["Load_kW"],
        mode="none", name="Shaved Peak Energy",
        showlegend=False
    ),
    row=1, col=1
)

# Peak Demand Limit Line
fig.add_shape(
    type="line",
    x0=0, x1=48,
    y0=new_max_demand, y1=new_max_demand,
    line=dict(color="#ff9f43", width=2, dash="dot"),
    row=1, col=1
)

# Row 2: BESS State of Charge (%)
fig.add_trace(
    go.Scatter(
        x=df_sim["Time_Hr"], y=df_sim["SoC_Pct"],
        mode="lines", name="BESS SoC (%)",
        line=dict(color="#2e86de", width=2.5),
        fill="tozeroy", fillcolor="rgba(46, 134, 222, 0.15)"
    ),
    row=2, col=1
)

# Add Min/Max SoC Limit Lines
fig.add_shape(
    type="line", x0=0, x1=48, y0=soc_min_pct, y1=soc_min_pct,
    line=dict(color="#ee5253", width=1.5, dash="dash"), row=2, col=1
)
fig.add_shape(
    type="line", x0=0, x1=48, y0=soc_max_pct, y1=soc_max_pct,
    line=dict(color="#10ac84", width=1.5, dash="dash"), row=2, col=1
)

# Update layout styling
fig.update_layout(
    height=600,
    margin=dict(l=40, r=40, t=50, b=40),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    hovermode="x unified",
    template="plotly_white"
)

fig.update_xaxes(title_text="Simulation Time (Hours)", row=2, col=1, dtick=4)
fig.update_yaxes(title_text="Power (kW)", row=1, col=1)
fig.update_yaxes(title_text="SoC (%)", range=[0, 105], row=2, col=1)

st.plotly_chart(fig, use_container_width=True)

# Detailed Data View Option
with st.expander("📋 View Raw Data Table"):
    st.dataframe(df_sim.style.highlight_max(axis=0, subset=["Load_kW", "Grid_kW"], color="#ffcdd2"))

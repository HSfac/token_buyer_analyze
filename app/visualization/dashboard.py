import dash
from dash import html, dcc
import dash_bootstrap_components as dbc
import plotly.express as px
import plotly.graph_objects as go
from app.models.types import BuyerAnalysis

def create_dashboard(analysis: BuyerAnalysis):
    app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
    
    # 데이터 준비
    ranges = list(analysis.buyers_by_sol_range.keys())
    counts = [range_data.count for range_data in analysis.buyers_by_sol_range.values()]
    total_sols = [range_data.total_sol for range_data in analysis.buyers_by_sol_range.values()]
    
    # 매수자 수 분포 차트
    fig_counts = px.bar(
        x=ranges,
        y=counts,
        title='SOL 구간별 매수자 수',
        labels={'x': 'SOL 구간', 'y': '매수자 수'},
        color=ranges,
        color_discrete_sequence=px.colors.qualitative.Set3
    )
    
    # SOL 매수량 분포 차트
    fig_sols = px.bar(
        x=ranges,
        y=total_sols,
        title='SOL 구간별 총 매수량',
        labels={'x': 'SOL 구간', 'y': '총 매수량 (SOL)'},
        color=ranges,
        color_discrete_sequence=px.colors.qualitative.Set3
    )
    
    # 파이 차트 (매수자 비율)
    fig_pie = px.pie(
        values=counts,
        names=ranges,
        title='매수자 구간별 비율',
        color_discrete_sequence=px.colors.qualitative.Set3
    )
    
    # 상세 지갑 정보 컴포넌트 생성
    wallet_details = []
    for range_key, range_data in analysis.buyers_by_sol_range.items():
        wallet_details.append(
            html.Div([
                html.H5(f'{range_key} SOL 구간'),
                html.P(f'매수자 수: {range_data.count}'),
                html.P(f'총 매수량: {range_data.total_sol:.2f} SOL'),
                html.Details([
                    html.Summary('지갑 목록'),
                    html.Ul([html.Li(wallet) for wallet in range_data.wallets])
                ])
            ])
        )
    
    # 대시보드 레이아웃
    app.layout = dbc.Container([
        html.H1('토큰 매수자 분석 대시보드', className='text-center my-4'),
        
        dbc.Row([
            dbc.Col([
                html.H4('분석 정보'),
                html.P(f'토큰 주소: {analysis.token}'),
                html.P(f'분석 시간: {analysis.snapshot_time}'),
                html.P(f'총 매수자 수: {sum(counts)}'),
                html.P(f'총 매수량: {sum(total_sols):.2f} SOL')
            ], width=12, className='mb-4')
        ]),
        
        dbc.Row([
            dbc.Col([
                dcc.Graph(figure=fig_counts)
            ], width=6),
            dbc.Col([
                dcc.Graph(figure=fig_sols)
            ], width=6)
        ]),
        
        dbc.Row([
            dbc.Col([
                dcc.Graph(figure=fig_pie)
            ], width=12)
        ]),
        
        dbc.Row([
            dbc.Col([
                html.H4('상세 지갑 정보'),
                *wallet_details
            ], width=12)
        ])
    ], fluid=True)
    
    return app 
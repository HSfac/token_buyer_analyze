import dash
from dash import html, dcc, Output, Input
import dash_bootstrap_components as dbc
import plotly.express as px
import plotly.graph_objects as go
from app.models.types import BuyerAnalysis
import pandas as pd
from datetime import datetime
from urllib.parse import parse_qsl
import logging

logger = logging.getLogger(__name__)

def create_dashboard(analysis: BuyerAnalysis):
    app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
    
    # 캐시 비활성화
    app.config.suppress_callback_exceptions = True
    app.server.config['CACHE_TYPE'] = 'null'
    
    # 데이터 준비
    ranges = list(analysis.buyers_by_sol_range.keys())
    counts = [range_data.count for range_data in analysis.buyers_by_sol_range.values()]
    total_sols = [range_data.total_sol for range_data in analysis.buyers_by_sol_range.values()]
    
    # CSV 데이터 준비
    csv_data = []
    
    # 1. 토큰 정보
    csv_data.append({
        '분석 유형': '토큰 정보',
        '토큰 주소': analysis.token,
        '분석 시간': analysis.snapshot_time,
        '시작 시간': analysis.time_range.start_time.isoformat(),
        '종료 시간': analysis.time_range.end_time.isoformat(),
        '총 매수자 수': sum(counts),
        '총 매수량 (SOL)': sum(total_sols),
        '총 매도량 (SOL)': analysis.total_sell_volume,
        '순 매수량 (SOL)': analysis.net_buy_volume,
        '고유 매수자 수': analysis.unique_buyers,
        '고유 매도자 수': analysis.unique_sellers
    })
    
    # 2. SOL 구간별 요약 정보
    for range_key, range_data in analysis.buyers_by_sol_range.items():
        csv_data.append({
            '분석 유형': 'SOL 구간 요약',
            'SOL 구간': range_key,
            '매수자 수': range_data.count,
            '총 매수량 (SOL)': range_data.total_sol,
            '평균 매수량 (SOL)': range_data.total_sol / range_data.count if range_data.count > 0 else 0,
            '지갑 주소': '',
            '개별 매수량 (SOL)': ''
        })
    
    # 3. 개별 지갑 상세 정보
    for range_key, range_data in analysis.buyers_by_sol_range.items():
        for wallet in range_data.wallets:
            # 지갑별 상세 정보 가져오기
            wallet_summary = analysis.wallet_summaries.get(wallet, {})
            
            csv_data.append({
                '분석 유형': '지갑 상세',
                'SOL 구간': range_key,
                '지갑 주소': wallet,
                '개별 매수량 (SOL)': range_data.total_sol / len(range_data.wallets) if range_data.wallets else 0,
                '총 매수량 (SOL)': '',
                '매수자 수': '',
                '평균 매수량 (SOL)': '',
                '시작 시간': '',
                '종료 시간': '',
                '총 매수자 수': '',
                '총 매도량 (SOL)': '',
                '순 매수량 (SOL)': '',
                '고유 매수자 수': '',
                '고유 매도자 수': ''
            })
    
    df = pd.DataFrame(csv_data)
    csv_string = df.to_csv(index=False)
    
    # 매수자 수 분포 차트
    fig_counts = px.bar(
        x=ranges,
        y=counts,
        title='SOL 구간별 매수자 수',
        labels={'x': 'SOL 구간', 'y': '매수자 수'},
        color_discrete_sequence=px.colors.qualitative.Set3
    )
    
    # SOL 매수량 분포 차트
    fig_sols = px.bar(
        x=ranges,
        y=total_sols,
        title='SOL 구간별 총 매수량',
        labels={'x': 'SOL 구간', 'y': '총 매수량 (SOL)'},
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
        html.Div(id='timestamp-hidden', children=str(datetime.now().timestamp()), style={'display': 'none'}),
        
        dbc.Row([
            dbc.Col([
                html.H4('분석 정보'),
                html.P(f'토큰 주소: {analysis.token}'),
                html.P(f'분석 시간: {analysis.snapshot_time}'),
                html.P(f'총 매수자 수: {sum(counts)}'),
                html.P(f'총 매수량: {sum(total_sols):.2f} SOL'),
                html.A(
                    'CSV 다운로드',
                    id='download-link',
                    download=f'token_analysis_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv',
                    href=f'data:text/csv;charset=utf-8,{csv_string}',
                    className='btn btn-success mb-3'
                ),
                html.Button('데이터 갱신', id='refresh-button', className='btn btn-primary ml-2 mb-3')
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
    
    @app.callback(
        Output('timestamp-hidden', 'children'),
        Input('refresh-button', 'n_clicks'),
        prevent_initial_call=True
    )
    def refresh_data(n_clicks):
        """데이터 갱신 버튼 클릭 시 호출"""
        if n_clicks:
            logger.info("대시보드 데이터 갱신 버튼 클릭됨")
            # 타임스탬프 업데이트로 전체 페이지 새로고침 유도
            return str(datetime.now().timestamp())
        return dash.no_update
    
    @app.callback(
        Output('download-link', 'href'),
        Input('timestamp-hidden', 'children'),
        prevent_initial_call=True
    )
    def update_download_link(timestamp):
        """타임스탬프 변경 시 다운로드 링크 업데이트"""
        if timestamp:
            logger.info("다운로드 링크 업데이트")
            # 새로운 파일명 생성
            new_filename = f'token_analysis_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            return f'data:text/csv;charset=utf-8,{csv_string}'
        return dash.no_update
    
    return app 
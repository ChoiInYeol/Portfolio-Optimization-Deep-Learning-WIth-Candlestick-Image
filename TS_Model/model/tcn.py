import torch
from torch import nn
from torch.nn.utils import weight_norm
import torch.nn.functional as F
from typing import List, Dict, Any


class PortfolioTCN(nn.Module):
    def __init__(
        self,
        n_feature: int,
        n_output: int,
        num_channels: List[int],
        kernel_size: int,
        n_dropout: float,
        n_timestep: int,
        constraints: Dict[str, Any] = None
    ):
        """
        TCN 기반 포트폴리오 최적화 모델
        
        Args:
            n_feature: 입력 특성 수
            n_output: 출력 차원 (종목 수)
            num_channels: TCN 채널 수 리스트
            kernel_size: 컨볼루션 커널 크기
            n_dropout: 드롭아웃 비율
            n_timestep: 시계열 길이
            constraints: 포트폴리오 제약조건
                - long_only: bool
                - max_position: float
                - cardinality: int
                - leverage: float
        """
        super().__init__()
        self.input_size = n_feature
        self.n_stocks = n_output
        self.constraints = constraints or {}
        
        # Score Block (h1)
        self.tcn = TemporalConvNet(
            num_inputs=n_feature,
            num_channels=num_channels,
            kernel_size=kernel_size,
            dropout=n_dropout
        )
        
        self.score_layer = nn.Linear(num_channels[-1], n_output)
        self.tempmaxpool = nn.MaxPool1d(n_timestep)
        self.swish = nn.SiLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        순전파
        
        Args:
            x: 입력 시퀀스 [batch_size, seq_len, n_stocks]
            
        Returns:
            포트폴리오 비중 [batch_size, n_stocks]
        """
        # Score Block (h1)
        output = self.tcn(x.transpose(1, 2))
        output = self.tempmaxpool(output).squeeze(-1)
        
        # 자산별 점수 생성
        scores = self.score_layer(output)
        
        # Portfolio Block (h2)
        weights = self.convert_scores_to_weights(scores)
        
        return weights
    
    def convert_scores_to_weights(self, scores: torch.Tensor) -> torch.Tensor:
        """점수를 포트폴리오 가중치로 변환합니다."""
        if self.constraints.get('long_only', True):
            # Long-only constraint with softmax
            weights = torch.softmax(scores, dim=-1)
            
        else:
            # General case allowing short positions with tanh
            weights = torch.tanh(scores)  # [-1, 1] 범위로 제한
            
            # Normalize to satisfy leverage constraint
            leverage = self.constraints.get('leverage', 1.0)
            weights = leverage * weights / weights.abs().sum(dim=-1, keepdim=True)
        
        if 'max_position' in self.constraints:
            # Maximum position constraint using generalized sigmoid
            u = self.constraints['max_position']
            a = (1 - u) / (self.n_stocks * u - 1)
            
            def phi_a(x):
                return (a + 1) / (1 + torch.exp(-x))
            
            weights = torch.sign(scores) * phi_a(scores.abs())
            weights = weights / weights.sum(dim=-1, keepdim=True)
        
        if 'cardinality' in self.constraints:
            # Cardinality constraint using top-k selection
            k = self.constraints['cardinality']
            values, indices = torch.topk(scores.abs(), k, dim=-1)
            mask = torch.zeros_like(scores).scatter_(-1, indices, 1.0)
            weights = weights * mask
            weights = weights / weights.sum(dim=-1, keepdim=True)
        
        return weights


class PortfolioTCNWithProb(PortfolioTCN):
    def __init__(
        self,
        n_feature: int,
        n_output: int,
        num_channels: List[int],
        kernel_size: int,
        n_dropout: float,
        n_timestep: int,
        constraints: Dict[str, Any] = None
    ):
        """상승확률을 활용하는 TCN 기반 포트폴리오 최적화 모델"""
        super().__init__(
            n_feature, n_output, num_channels,
            kernel_size, n_dropout, n_timestep, constraints
        )
        
        # 상승확률 인코딩을 위한 레이어
        self.prob_encoder = nn.Sequential(
            nn.Linear(n_output, num_channels[-1]),
            nn.SiLU(),
            nn.Dropout(n_dropout)
        )
        
        # 결합된 특성을 처리하기 위한 레이어
        self.score_layer = nn.Linear(num_channels[-1] * 2, n_output)

    def forward(self, x_returns: torch.Tensor, x_probs: torch.Tensor) -> torch.Tensor:
        """
        순전파
        
        Args:
            x_returns: 수익률 시퀀스 [batch_size, seq_len, n_stocks]
            x_probs: 상승확률 [batch_size, pred_len, n_stocks]
            
        Returns:
            포트폴리오 비중 [batch_size, n_stocks]
        """
        # TCN으로 수익률 시퀀스 처리
        output = self.tcn(x_returns.transpose(1, 2))
        output = self.tempmaxpool(output).squeeze(-1)
        
        # 상승확률 처리 (첫 번째 예측 시점의 확률 사용)
        prob_features = self.prob_encoder(x_probs[:, 0, :])
        
        # 특성 결합
        combined = torch.cat([output, prob_features], dim=1)
        
        # 자산별 점수 생성
        scores = self.score_layer(combined)
        
        # Portfolio Block (h2)
        weights = self.convert_scores_to_weights(scores)
        
        return weights


class Chomp1d(nn.Module):
    def __init__(self, chomp_size):
        super(Chomp1d, self).__init__()
        self.chomp_size = chomp_size

    def forward(self, x):
        return x[:, :, :-self.chomp_size].contiguous()


class TemporalBlock(nn.Module):
    def __init__(self, n_inputs, n_outputs, kernel_size, stride, dilation, padding, dropout=0.2):
        super(TemporalBlock, self).__init__()
        self.conv1 = weight_norm(nn.Conv1d(n_inputs, n_outputs, kernel_size,
                                           stride=stride, padding=padding, dilation=dilation))
        self.chomp1 = Chomp1d(padding)
        self.relu1 = nn.ReLU()
        self.dropout1 = nn.Dropout(dropout)

        self.conv2 = weight_norm(nn.Conv1d(n_outputs, n_outputs, kernel_size,
                                           stride=stride, padding=padding, dilation=dilation))
        self.chomp2 = Chomp1d(padding)
        self.relu2 = nn.ReLU()
        self.dropout2 = nn.Dropout(dropout)

        self.net = nn.Sequential(self.conv1, self.chomp1, self.relu1, self.dropout1,
                                 self.conv2, self.chomp2, self.relu2, self.dropout2)
        self.downsample = nn.Conv1d(n_inputs, n_outputs, 1) if n_inputs != n_outputs else None
        self.relu = nn.ReLU()
        self.init_weights()

    def init_weights(self):
        self.conv1.weight.data.normal_(0, 0.01)
        self.conv2.weight.data.normal_(0, 0.01)
        if self.downsample is not None:
            self.downsample.weight.data.normal_(0, 0.01)

    def forward(self, x):
        out = self.net(x)
        res = x if self.downsample is None else self.downsample(x)
        return self.relu(out + res)


class TemporalConvNet(nn.Module):
    def __init__(self, num_inputs, num_channels, kernel_size=2, dropout=0.2):
        super(TemporalConvNet, self).__init__()
        layers = []
        num_levels = len(num_channels)
        for i in range(num_levels):
            dilation_size = 2 ** i
            in_channels = num_inputs if i == 0 else num_channels[i - 1]
            out_channels = num_channels[i]
            layers += [TemporalBlock(in_channels, out_channels, kernel_size, stride=1, dilation=dilation_size,
                                     padding=(kernel_size - 1) * dilation_size, dropout=dropout)]

        self.network = nn.Sequential(*layers)

    def forward(self, x):
        return self.network(x)
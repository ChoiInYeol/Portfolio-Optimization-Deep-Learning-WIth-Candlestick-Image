"""
CNN 모델의 학습 관련 기능을 제공하는 모듈입니다.
"""

import torch
import torch.nn as nn
import torch.optim as optim
import time
import copy
from typing import Dict, List, Tuple, Optional, Any
from torch.utils.data import DataLoader
from tqdm import tqdm
import math

class CNNTrainer:
    """CNN 모델 학습을 위한 클래스입니다."""
    
    def __init__(self, model: nn.Module, device: torch.device, loss_name: str):
        """
        Args:
            model: 학습할 CNN 모델
            device: 학습에 사용할 디바이스
            loss_name: 손실 함수 이름
        """
        if torch.cuda.device_count() > 1:
            print(f"Using {torch.cuda.device_count()} GPUs for training")
            model = nn.DataParallel(model)
        self.model = model.to(device)  # 모델을 GPU로 이동
        self.device = device
        self.loss_name = loss_name

    def train_single_model(
        self,
        dataloaders_dict: Dict[str, DataLoader],
        model_save_path: str,
        optimizer: optim.Optimizer,
        max_epoch: int,
        early_stop: bool = True,
        enable_tqdm: bool = False
    ) -> Tuple[Dict[str, float], Dict[str, float], nn.Module]:
        """단일 모델을 학습합니다."""
        print(f"Training on device {self.device} under {model_save_path}")
        
        since = time.time()
        best_validate_metrics = {
            "loss": 10.0,
            "accy": 0.0,
            "MCC": 0.0,
            "epoch": 0,
            "diff": 0.0
        }
        
        # 학습 과정 추적을 위한 데이터 구조
        learning_curves = {
            "train": {"loss": [], "accuracy": [], "mcc": []},
            "validate": {"loss": [], "accuracy": [], "mcc": []}
        }
        
        confusion_matrices = {
            "train": {"final": None, "best": None},
            "validate": {"final": None, "best": None}
        }
        
        sample_stats = {
            "train": {"total": 0, "positive": 0, "negative": 0},
            "validate": {"total": 0, "positive": 0, "negative": 0}
        }
        
        best_model = copy.deepcopy(self.model.state_dict())
        
        # 학습 루프
        for epoch in range(max_epoch):
            epoch_pbar = tqdm(total=len(dataloaders_dict['train']) + len(dataloaders_dict['validate']),
                             desc=f'Epoch {epoch+1}/{max_epoch}')
            
            for phase in ["train", "validate"]:
                running_metrics = self._init_running_metrics()
                
                if phase == "train":
                    self.model.train()
                else:
                    self.model.eval()
                    
                # 배치별 진행률 표시
                for batch in dataloaders_dict[phase]:
                    inputs = batch["image"].to(self.device, dtype=torch.float)
                    labels = batch["label"].to(self.device, dtype=torch.long)
                    
                    with torch.set_grad_enabled(phase == "train"):
                        outputs = self.model(inputs)
                        loss = self.loss_from_model_output(labels, outputs)
                        _, preds = torch.max(outputs, 1)
                        
                        if phase == "train":
                            optimizer.zero_grad()
                            loss.backward()
                            optimizer.step()
                    
                    self._update_running_metrics(loss, labels, preds, running_metrics)
                    
                    # 현재 배치의 메트릭 계산
                    batch_metrics = self._calculate_batch_metrics(running_metrics, len(dataloaders_dict[phase].dataset))
                    
                    # tqdm 진행률 업데이트
                    epoch_pbar.set_postfix({
                        'phase': phase,
                        'loss': f'{batch_metrics["loss"]:.4f}',
                        'acc': f'{batch_metrics["accuracy"]:.4f}',
                        'MCC': f'{batch_metrics["mcc"]:.4f}'
                    })
                    epoch_pbar.update(1)
                
                # 에폭 종료 시 메트릭 계산
                epoch_metrics = self._generate_epoch_stat(
                    epoch, 
                    optimizer.param_groups[0]['lr'],
                    len(dataloaders_dict[phase].dataset),
                    running_metrics
                )
                
                # 에폭 종료 시 결과 출력
                phase_results = (
                    f"{phase.capitalize()}: "
                    f"Loss: {epoch_metrics['loss']:.4f}, "
                    f"Acc: {epoch_metrics['accy']:.4f}, "
                    f"MCC: {epoch_metrics['MCC']:.4f}"
                )
                tqdm.write(phase_results)
            
            epoch_pbar.close()
            
            # 학습 곡선 업데이트
            learning_curves[phase]["loss"].append(epoch_metrics["loss"])
            learning_curves[phase]["accuracy"].append(epoch_metrics["accy"])
            learning_curves[phase]["mcc"].append(epoch_metrics["MCC"])
            
            # 혼동 행렬 저장
            confusion_matrices[phase]["final"] = {
                "TP": running_metrics["TP"],
                "TN": running_metrics["TN"],
                "FP": running_metrics["FP"],
                "FN": running_metrics["FN"]
            }
            
            # 최고 성능 모델 저장
            if phase == "validate" and epoch_metrics["loss"] < best_validate_metrics["loss"]:
                for metric in best_validate_metrics.keys():
                    best_validate_metrics[metric] = epoch_metrics[metric]
                best_model = copy.deepcopy(self.model.state_dict())
                
                # 최고 성능 시점의 혼동 행렬 저장
                confusion_matrices["validate"]["best"] = confusion_matrices["validate"]["final"]
            
            # Early stopping 체크
            if early_stop and (epoch - best_validate_metrics["epoch"]) >= 3:
                break
        
        # 최종 결과 저장 및 반환
        training_summary = {
            "confusion_matrices": confusion_matrices,
            "sample_stats": sample_stats,
            "learning_curves": learning_curves
        }
        
        self.model.load_state_dict(best_model)
        best_validate_metrics["model_state_dict"] = self.model.state_dict()
        best_validate_metrics["training_summary"] = training_summary
        
        torch.save(best_validate_metrics, model_save_path)
        
        train_metrics = self.evaluate(dataloaders_dict["train"])
        train_metrics["epoch"] = best_validate_metrics["epoch"]
        
        return train_metrics, best_validate_metrics, self.model

    def loss_from_model_output(self, labels: torch.Tensor, outputs: torch.Tensor) -> torch.Tensor:
        """
        모델 출력에 대한 손실을 계산합니다.

        Args:
            labels: 정답 레이블
            outputs: 모델 출력값

        Returns:
            계산된 손실값
        """
        if self.loss_name == "kldivloss":
            log_prob = nn.LogSoftmax(dim=1)(outputs)
            target = self._binary_one_hot(labels.view(-1, 1))
            target = target.to(torch.float)
            loss = nn.KLDivLoss()(log_prob, target)
        elif self.loss_name == "multimarginloss":
            loss = nn.MultiMarginLoss(margin=self.margin)(outputs, labels)
        elif self.loss_name == "cross_entropy":
            loss = nn.CrossEntropyLoss()(outputs, labels)
        elif self.loss_name == "MSE":
            loss = nn.MSELoss()(outputs.flatten(), labels)
        else:
            raise ValueError(f"Unknown loss function: {self.loss_name}")
        return loss

    def evaluate(self, dataloader: DataLoader) -> Dict[str, float]:
        """
        모델을 평가합니다.

        Args:
            dataloader: 평가용 데이터로더

        Returns:
            평가 메트릭
        """
        self.model.eval()
        running_metrics = self._init_running_metrics()

        with torch.no_grad():
            for batch in dataloader:
                inputs = batch["image"].to(self.device, dtype=torch.float)
                labels = batch["label"].to(self.device, dtype=torch.long)
                outputs = self.model(inputs)
                loss = self.loss_from_model_output(labels, outputs)
                _, preds = torch.max(outputs, 1)
                self._update_running_metrics(loss, labels, preds, running_metrics)

        num_samples = len(dataloader.dataset)
        metrics = self._generate_epoch_stat(-1, -1, num_samples, running_metrics)
        return metrics

    @staticmethod
    def _init_running_metrics() -> Dict[str, float]:
        """초기 메트릭 딕셔너리를 생성합니다."""
        return {
            "running_loss": 0.0,
            "running_correct": 0.0,
            "TP": 0,
            "TN": 0,
            "FP": 0,
            "FN": 0,
        }

    @staticmethod
    def _update_running_metrics(
        loss: torch.Tensor,
        labels: torch.Tensor,
        preds: torch.Tensor,
        running_metrics: Dict[str, float]
    ) -> None:
        """
        실행 중인 메트릭을 업데이트합니다.

        Args:
            loss: 손실값
            labels: 정답 레이블
            preds: 예측값
            running_metrics: 업데이트할 메트릭 딕셔너리
        """
        running_metrics["running_loss"] += loss.item() * len(labels)
        running_metrics["running_correct"] += (preds == labels).sum().item()
        running_metrics["TP"] += (preds * labels).sum().item()
        running_metrics["TN"] += ((preds - 1) * (labels - 1)).sum().item()
        running_metrics["FP"] += (preds * (labels - 1)).sum().abs().item()
        running_metrics["FN"] += ((preds - 1) * labels).sum().abs().item()

    @staticmethod
    def _generate_epoch_stat(
        epoch: int,
        learning_rate: float,
        num_samples: int,
        running_metrics: Dict[str, float]
    ) -> Dict[str, float]:
        """
        에폭 통계를 생성합니다.

        Args:
            epoch: 현재 에폭
            learning_rate: 학습률
            num_samples: 샘플 수
            running_metrics: 현재까지의 메트릭

        Returns:
            에폭 통계 딕셔너리
        """
        TP, TN, FP, FN = (
            running_metrics["TP"],
            running_metrics["TN"],
            running_metrics["FP"],
            running_metrics["FN"],
        )
        
        epoch_stat = {
            "epoch": epoch,
            "lr": "{:.2E}".format(learning_rate),
            "diff": 1.0 * ((TP + FP) - (TN + FN)) / num_samples,
            "loss": running_metrics["running_loss"] / num_samples,
            "accy": 1.0 * running_metrics["running_correct"] / num_samples,
        }
        
        denominator = math.sqrt((TP + FP) * (TP + FN) * (TN + FP) * (TN + FN))
        epoch_stat["MCC"] = (
            float('nan') if denominator == 0
            else 1.0 * (TP * TN - FP * FN) / denominator
        )
        
        return epoch_stat

    def _binary_one_hot(self, labels: torch.Tensor) -> torch.Tensor:
        """
        이진 레이블을 원-핫 인코딩으로 변환합니다.

        Args:
            labels: 이진 레이블 텐서

        Returns:
            원-핫 인코딩된 텐서
        """
        one_hot = torch.zeros(labels.size(0), 2, device=self.device)
        one_hot.scatter_(1, labels, 1)
        return one_hot

    def _calculate_batch_metrics(self, running_metrics: Dict[str, float], total_samples: int) -> Dict[str, float]:
        """
        배치별 메트릭을 계산합니다.
        
        Args:
            running_metrics: 현재까지의 누적 메트릭
            total_samples: 전체 샘플 수
        
        Returns:
            배치 메트릭 딕셔너리
        """
        TP, TN, FP, FN = (
            running_metrics["TP"],
            running_metrics["TN"],
            running_metrics["FP"],
            running_metrics["FN"]
        )
        
        current_samples = TP + TN + FP + FN
        
        metrics = {
            "loss": running_metrics["running_loss"] / current_samples,
            "accuracy": running_metrics["running_correct"] / current_samples
        }
        
        denominator = math.sqrt((TP + FP) * (TP + FN) * (TN + FP) * (TN + FN))
        metrics["mcc"] = (
            0.0 if denominator == 0
            else (TP * TN - FP * FN) / denominator
        )
        
        return metrics
import torch
import torch.nn as nn
import numpy as np

from torchsummary import summary
import json
import os
from Misc import config as cf


class Model(object):
    def __init__(
        self,
        ws,
        layer_number=1,
        inplanes=cf.TRUE_DATA_CNN_INPLANES,
        drop_prob=0.50,
        filter_size=None,
        stride=None,
        dilation=None,
        max_pooling=None,
        filter_size_list=None,
        stride_list=None,
        dilation_list=None,
        max_pooling_list=None,
        batch_norm=True,
        xavier=True,
        lrelu=True,
        ts1d_model=False,
        bn_loc="bn_bf_relu",
        conv_layer_chanls=None,
        regression_label=None,
    ):
        self.ws = ws
        self.layer_number = layer_number
        self.inplanes = inplanes
        self.drop_prob = drop_prob
        self.filter_size_list = (
            [filter_size] * self.layer_number
            if filter_size_list is None
            else filter_size_list
        )
        self.stride_list = (
            [stride] * self.layer_number if stride_list is None else stride_list
        )
        self.max_pooling_list = (
            [max_pooling] * self.layer_number
            if max_pooling_list is None
            else max_pooling_list
        )
        self.dilation_list = (
            [dilation] * self.layer_number if dilation_list is None else dilation_list
        )
        self.batch_norm = batch_norm
        self.xavier = xavier
        self.lrelu = lrelu
        self.ts1d_model = ts1d_model
        self.bn_loc = bn_loc
        self.conv_layer_chanls = conv_layer_chanls
        self.regression_label = regression_label
        assert self.regression_label in [None, "raw_ret", "vol_adjust_ret"]

        self.padding_list = (
            [int(fs / 2) for fs in self.filter_size_list]
            if self.ts1d_model
            else [(int(fs[0] / 2), int(fs[1] / 2)) for fs in self.filter_size_list]
        )
        self.name = get_full_model_name(
            self.ts1d_model,
            self.ws,
            self.layer_number,
            self.inplanes,
            self.filter_size_list,
            self.max_pooling_list,
            self.stride_list,
            self.dilation_list,
            drop_prob=self.drop_prob,
            batch_norm=self.batch_norm,
            xavier=self.xavier,
            lrelu=self.lrelu,
            bn_loc=self.bn_loc,
            conv_layer_chanls=conv_layer_chanls,
            regression_label=self.regression_label,
        )

        self.input_size = self.get_input_size()

    def init_model(self, device=None, state_dict=None):
        if self.ts1d_model:
            model = CNN1DModel(
                self.layer_number,
                self.input_size,
                inplanes=self.inplanes,
                drop_prob=self.drop_prob,
                filter_size_list=self.filter_size_list,
                stride_list=self.stride_list,
                padding_list=self.padding_list,
                dilation_list=self.dilation_list,
                max_pooling_list=self.max_pooling_list,
                regression_label=self.regression_label,
            )
        else:
            model = CNNModel(
                self.layer_number,
                self.input_size,
                inplanes=self.inplanes,
                drop_prob=self.drop_prob,
                filter_size_list=self.filter_size_list,
                stride_list=self.stride_list,
                padding_list=self.padding_list,
                dilation_list=self.dilation_list,
                max_pooling_list=self.max_pooling_list,
                batch_norm=self.batch_norm,
                xavier=self.xavier,
                lrelu=self.lrelu,
                bn_loc=self.bn_loc,
                conv_layer_chanls=self.conv_layer_chanls,
                regression_label=self.regression_label,
            )

        if state_dict is not None:
            for i in range(self.layer_number - 1):
                print("Loading layer {}".format(i))
                for j in [0, 1]:
                    model.conv_layers[i][j].weight = torch.nn.Parameter(
                        state_dict["conv_layers.{}.{}.weight".format(i, j)]
                    )
                    model.conv_layers[i][j].bias = torch.nn.Parameter(
                        state_dict["conv_layers.{}.{}.bias".format(i, j)]
                    )

        if device is not None:
            model.to(device)

        return model

    def init_model_with_model_state_dict(self, model_state_dict, device=None):
        model = self.init_model(device=device)
        print("Loading model from model_state_dict")
        model.load_state_dict(model_state_dict)
        return model

    def get_input_size(self):
        if self.ts1d_model:
            input_size_dict = {5: (6, 5), 20: (6, 20), 60: (6, 60)}
        else:
            input_size_dict = {5: (32, 15), 20: (64, 60), 60: (96, 180)}
        return input_size_dict[self.ws]

    def model_summary(self):
        print(self.name)
        if self.ts1d_model:
            img_size_dict = {5: (6, 5), 20: (6, 20), 60: (6, 60)}
        else:
            img_size_dict = {5: (1, 32, 15), 20: (1, 64, 60), 60: (1, 96, 180)}
        device = torch.device(
            "cuda:{}".format(0) if torch.cuda.is_available() else "cpu"
        )
        model = self.init_model()
        model.to(device)
        print(model)
        summary(model, img_size_dict[self.ws])


def init_weights(m):
    if type(m) in [nn.Conv2d, nn.Conv1d]:
        nn.init.xavier_uniform_(m.weight)
    elif type(m) == nn.Linear:
        nn.init.xavier_uniform_(m.weight)
        m.bias.data.fill_(0.01)


class Flatten(nn.Module):
    def __init__(self):
        super(Flatten, self).__init__()

    def forward(self, x):
        return x.view(x.shape[0], np.prod(x.shape[1:]))


class CNNModel(nn.Module):
    def __init__(
        self,
        layer_number,
        input_size,
        inplanes=cf.TRUE_DATA_CNN_INPLANES,
        drop_prob=0.50,
        filter_size_list=[(3, 3)],
        stride_list=[(1, 1)],
        padding_list=[(1, 1)],
        dilation_list=[(1, 1)],
        max_pooling_list=[(2, 2)],
        batch_norm=True,
        xavier=True,
        lrelu=True,
        conv_layer_chanls=None,
        bn_loc="bn_bf_relu",
        regression_label=None,
    ):

        self.layer_number = layer_number
        self.input_size = input_size
        self.conv_layer_chanls = conv_layer_chanls
        super(CNNModel, self).__init__()
        self.conv_layers = self._init_conv_layers(
            layer_number,
            inplanes,
            drop_prob,
            filter_size_list,
            stride_list,
            padding_list,
            dilation_list,
            max_pooling_list,
            batch_norm,
            lrelu,
            bn_loc,
        )
        fc_size = self._get_conv_layers_flatten_size()
        if regression_label is not None:
            self.fc = nn.Linear(fc_size, 1)
        else:
            self.fc = nn.Linear(fc_size, 2)
        if xavier:
            self.conv_layers.apply(init_weights)
            self.fc.apply(init_weights)

    @staticmethod
    def conv_layer(
        in_chanl: int,
        out_chanl: int,
        lrelu=True,
        double_conv=False,
        batch_norm=True,
        bn_loc="bn_bf_relu",
        filter_size=(3, 3),
        stride=(1, 1),
        padding=1,
        dilation=1,
        max_pooling=(2, 2),
    ):
        assert bn_loc in ["bn_bf_relu", "bn_af_relu", "bn_af_mp"]

        if not batch_norm:
            conv = [
                nn.Conv2d(
                    in_chanl,
                    out_chanl,
                    filter_size,
                    stride=stride,
                    padding=padding,
                    dilation=dilation,
                ),
                nn.LeakyReLU() if lrelu else nn.ReLU(),
            ]
        else:
            if bn_loc == "bn_bf_relu":
                conv = [
                    nn.Conv2d(
                        in_chanl,
                        out_chanl,
                        filter_size,
                        stride=stride,
                        padding=padding,
                        dilation=dilation,
                    ),
                    nn.BatchNorm2d(out_chanl),
                    nn.LeakyReLU() if lrelu else nn.ReLU(),
                ]
            elif bn_loc == "bn_af_relu":
                conv = [
                    nn.Conv2d(
                        in_chanl,
                        out_chanl,
                        filter_size,
                        stride=stride,
                        padding=padding,
                        dilation=dilation,
                    ),
                    nn.LeakyReLU() if lrelu else nn.ReLU(),
                    nn.BatchNorm2d(out_chanl),
                ]
            else:
                conv = [
                    nn.Conv2d(
                        in_chanl,
                        out_chanl,
                        filter_size,
                        stride=stride,
                        padding=padding,
                        dilation=dilation,
                    ),
                    nn.LeakyReLU() if lrelu else nn.ReLU(),
                ]

        layers = conv * 2 if double_conv else conv

        if max_pooling != (1, 1):
            layers.append(nn.MaxPool2d(max_pooling, ceil_mode=True))

        if batch_norm and bn_loc == "bn_af_mp":
            layers.append(nn.BatchNorm2d(out_chanl))

        return nn.Sequential(*layers)

    def _init_conv_layers(
        self,
        layer_number,
        inplanes,
        drop_prob,
        filter_size_list,
        stride_list,
        padding_list,
        dilation_list,
        max_pooling_list,
        batch_norm,
        lrelu,
        bn_loc,
    ):
        if self.conv_layer_chanls is None:
            conv_layer_chanls = [inplanes * (2**i) for i in range(layer_number)]
        else:
            assert len(self.conv_layer_chanls) == layer_number
            conv_layer_chanls = self.conv_layer_chanls
        layers = []
        prev_chanl = 1
        for i, conv_chanl in enumerate(conv_layer_chanls):
            layers.append(
                self.conv_layer(
                    prev_chanl,
                    conv_chanl,
                    filter_size=filter_size_list[i],
                    stride=stride_list[i],
                    padding=padding_list[i],
                    dilation=dilation_list[i],
                    max_pooling=max_pooling_list[i],
                    batch_norm=batch_norm,
                    lrelu=lrelu,
                    bn_loc=bn_loc,
                )
            )
            prev_chanl = conv_chanl
        layers.append(Flatten())
        layers.append(nn.Dropout(p=drop_prob))
        return nn.Sequential(*layers)

    def _get_conv_layers_flatten_size(self):
        dummy_input = torch.rand((1, 1, self.input_size[0], self.input_size[1]))
        x = self.conv_layers(dummy_input)
        return x.shape[1]

    def forward(self, x):
        x = self.conv_layers(x)
        x = self.fc(x)
        return x


class CNN1DModel(nn.Module):
    def __init__(
        self,
        layer_number,
        input_size,
        inplanes=cf.TRUE_DATA_CNN_INPLANES,
        drop_prob=0.5,
        filter_size_list=[3],
        stride_list=[1],
        padding_list=[1],
        dilation_list=[1],
        max_pooling_list=[2],
        regression_label=None,
    ):
        self.layer_number = layer_number
        self.input_size = input_size
        super(CNN1DModel, self).__init__()

        self.conv_layers = self._init_ts1d_conv_layers(
            layer_number,
            inplanes,
            drop_prob,
            filter_size_list,
            stride_list,
            padding_list,
            dilation_list,
            max_pooling_list,
        )
        fc_size = self._get_ts1d_conv_layers_flatten_size()
        if regression_label is not None:
            self.fc = nn.Linear(fc_size, 1)
        else:
            self.fc = nn.Linear(fc_size, 2)
        self.conv_layers.apply(init_weights)
        self.fc.apply(init_weights)

    @staticmethod
    def conv_layer_1d(
        in_chanl,
        out_chanl,
        filter_size=3,
        stride=1,
        padding=1,
        dilation=1,
        max_pooling=2,
    ):
        layers = [
            nn.Conv1d(
                in_chanl,
                out_chanl,
                filter_size,
                stride=stride,
                padding=padding,
                dilation=dilation,
            ),
            nn.BatchNorm1d(out_chanl),
            nn.LeakyReLU(),
            nn.MaxPool1d(max_pooling, ceil_mode=True),
        ]
        return nn.Sequential(*layers)

    def _init_ts1d_conv_layers(
        self,
        layer_number,
        inplanes,
        drop_prob,
        filter_size_list,
        stride_list,
        padding_list,
        dilation_list,
        max_pooling_list,
    ):
        conv_layer_chanls = [inplanes * (2**i) for i in range(layer_number)]
        layers = []
        prev_chanl = 6
        for i, conv_chanl in enumerate(conv_layer_chanls):
            layers.append(
                self.conv_layer_1d(
                    prev_chanl,
                    conv_chanl,
                    filter_size=filter_size_list[i],
                    stride=stride_list[i],
                    padding=padding_list[i],
                    dilation=dilation_list[i],
                    max_pooling=max_pooling_list[i],
                )
            )
            prev_chanl = conv_chanl
        layers.append(Flatten())
        layers.append(nn.Dropout(p=drop_prob))
        return nn.Sequential(*layers)

    def _get_ts1d_conv_layers_flatten_size(self):
        dummy_input = torch.rand((1, self.input_size[0], self.input_size[1]))
        x = self.conv_layers(dummy_input)
        return x.shape[1]

    def forward(self, x):
        x = self.conv_layers(x)
        x = self.fc(x)
        return x

def simplify_model_details(model_details):
    """반복되는 리스트 값들을 압축하여 가독성을 개선합니다."""
    simplified = model_details.copy()
    
    # 리스트 압축 표현식 사용
    simplified['filter_size_list'] = "[[5, 3]] * 10"
    simplified['max_pooling_list'] = "[[2, 1]] * 10"
    simplified['stride_list'] = "[[3, 1]] + [[1, 1]] * 10"
    simplified['dilation_list'] = "[[2, 1]] + [[1, 1]] * 10"
    
    # JSON에 저장할 때는 문자열 형태로 저장되고, 나중에 로드할 때 eval() 사용
    return {
        **simplified,
        "_comment": "압축된 리스트 표현식은 Python eval() 함수로 평가해야 합니다."
    }

def get_full_model_name(
    ts1d_model,
    ws,
    layer_number,
    inplanes,
    filter_size_list,
    max_pooling_list,
    stride_list,
    dilation_list,
    drop_prob=0.5,
    batch_norm=True,
    xavier=True,
    lrelu=True,
    bn_loc="bn_bf_relu",
    conv_layer_chanls=None,
    regression_label=None,
):
    model_type = "TS" if ts1d_model else "CNN"
    model_name = f"{model_type}{ws}"
    
    # 모델 세부 정보를 딕셔너리로 저장
    model_details = {
        "model_type": model_type,
        "window_size": ws,
        "layer_number": layer_number,
        "inplanes": inplanes,
        "filter_size_list": filter_size_list,
        "max_pooling_list": max_pooling_list,
        "stride_list": stride_list,
        "dilation_list": dilation_list,
        "drop_prob": drop_prob,
        "batch_norm": batch_norm,
        "xavier": xavier,
        "lrelu": lrelu,
        "bn_loc": bn_loc,
        "conv_layer_chanls": conv_layer_chanls,
        "regression_label": regression_label
    }
    
    # JSON 파일로 저장
    json_dir = os.path.join(cf.WORK_DIR, model_name)
    os.makedirs(json_dir, exist_ok=True)
    json_path = os.path.join(json_dir, "model_details.json")
    
    with open(json_path, 'w', encoding='utf-8') as json_file:
        simplified_details = simplify_model_details(model_details)
        json.dump(simplified_details, json_file, indent=2, ensure_ascii=False)
    
    print(f"모델 세부 정보가 {json_path}에 저장되었습니다.")
    
    return model_name


def all_layers(model):
    all_layers = []

    def remove_sequential(network):
        for layer in network.children():
            if isinstance(
                layer, nn.Sequential
            ):
                remove_sequential(layer)
            if list(layer.children()) == []:
                all_layers.append(layer)

    remove_sequential(model)
    return all_layers


def benchmark_model_summary():
    for ws in [5, 20, 60]:
        check_model_summary(
            ws,
            cf.BENCHMARK_MODEL_LAYERNUM_DICT[ws],
            cf.EMP_CNN_BL_SETTING[ws],
            inplanes=64,
        )


def check_model_summary(ws, layer_num, fs_s_d_mp, inplanes=cf.TRUE_DATA_CNN_INPLANES):
    fs, stride, dilation, mp = fs_s_d_mp
    model_obj = Model(
        ws,
        layer_num,
        inplanes=inplanes,
        filter_size_list=fs,
        stride_list=stride,
        dilation_list=dilation,
        max_pooling_list=mp,
    )
    model_obj.model_summary()


def main():
    pass


if __name__ == "__main__":
    main()

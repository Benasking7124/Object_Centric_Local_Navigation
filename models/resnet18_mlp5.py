import torch
import torch.nn as nn
from torchvision.models import resnet18

class CrossAttentionBlock(nn.Module):
    def __init__(self, embed_dim, num_heads=8):
        super(CrossAttentionBlock, self).__init__()
        self.mha = nn.MultiheadAttention(embed_dim, num_heads)

    def forward(self, query, key_value):
        # query and key_value: (B, C, H, W)
        B, C, H, W = query.shape

        # (H*W, B, C)
        query_flat = query.view(B, C, -1).permute(2, 0, 1)

        key_value_flat = key_value.view(B, C, -1).permute(2, 0, 1)
        attn_output, _ = self.mha(query_flat, key_value_flat, key_value_flat)

        # (B, C, H, W)
        attn_output = attn_output.permute(1, 2, 0).view(B, C, H, W)
        return attn_output

class Resnet18Mlp5(nn.Module):
    def __init__(self):
        super(Resnet18Mlp5, self).__init__()
        # Shared ResNet18 trunk (excluding the last 2 layers)
        base_resnet = resnet18(weights=None)
        self.shared_trunk = nn.Sequential(*list(base_resnet.children())[:-2])
        self.global_pool = nn.AdaptiveAvgPool2d((1, 1))
        num_trunk_channels = 512
        self.num_cameras = 5

        # Camera-specific heads for current images
        self.current_heads = nn.ModuleList([
            nn.Sequential(
                nn.Conv2d(num_trunk_channels, num_trunk_channels, kernel_size=1),
                nn.ReLU(inplace=True)
            ) for _ in range(self.num_cameras)
        ])

        # Camera-specific heads for the goal image
        self.goal_heads = nn.ModuleList([
            nn.Sequential(
                nn.Conv2d(num_trunk_channels, num_trunk_channels, kernel_size=1),
                nn.ReLU(inplace=True)
            ) for _ in range(self.num_cameras)
        ])

        # Cross-attention block shared across cameras
        self.cross_attention = CrossAttentionBlock(embed_dim=num_trunk_channels, num_heads=8)

        # Fully connected layers.
        # Input feature dimension: 5 cameras * 2 (current + goal) * 512 = 5120.
        self.fc_layer1 = nn.Sequential(
            nn.Linear(5120, 1024),
            nn.ReLU()
        )
        self.fc_layer2 = nn.Sequential(
            nn.Linear(1024, 1024),
            nn.ReLU()
        )
        self.fc_layer3 = nn.Sequential(
            nn.Linear(1024, 1024),
            nn.ReLU()
        )
        self.fc_layer4 = nn.Sequential(
            nn.Linear(1024, 1024),
            nn.ReLU()
        )
        # Final output layer produces 3 regression outputs.
        self.fc_layer5 = nn.Linear(1024, 3)

    def forward(self, current_images, goal_image):
        batch_size = current_images.size(0)
        current_features_list = []
        goal_features_list = []

        # Processing the goal image once through the shared trunk.
        goal_trunk_feat = self.shared_trunk(goal_image)  # (B, 512, H', W')

        for cam_idx in range(self.num_cameras):
            # Processing current image for camera cam_idx.
            curr = current_images[:, cam_idx, :, :, :]  # (B, C, H, W)
            curr_feat = self.shared_trunk(curr)         # (B, 512, H', W')
            curr_feat = self.current_heads[cam_idx](curr_feat)

            # Processing the same goal image
            goal_feat = self.goal_heads[cam_idx](goal_trunk_feat)

            # Applying cross-attention in both directions.
            curr_attended = curr_feat + self.cross_attention(curr_feat, goal_feat)
            goal_attended = goal_feat + self.cross_attention(goal_feat, curr_feat)

            # Global pooling.
            curr_pooled = self.global_pool(curr_attended).view(batch_size, -1)  # (B, 512)
            goal_pooled = self.global_pool(goal_attended).view(batch_size, -1)  # (B, 512)

            current_features_list.append(curr_pooled)
            goal_features_list.append(goal_pooled)

        # Concatenating features from all cameras.
        current_features = torch.cat(current_features_list, dim=1)  # (B, 5*512)
        goal_features = torch.cat(goal_features_list, dim=1)        # (B, 5*512)
        features = torch.cat([current_features, goal_features], dim=1)  # (B, 5120)

        # Fully connected layers.
        x = self.fc_layer1(features)
        x = self.fc_layer2(x)
        x = self.fc_layer3(x)
        x = self.fc_layer4(x)
        output = self.fc_layer5(x)  # (B, 3)
        return output